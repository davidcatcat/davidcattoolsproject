import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from argparse import Namespace
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool.providers.base import Reply
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import (_record, result_head_admitted, run_task_complete,
                                         run_task_review_prompt)
from llmtool import task_completion
from llmtool import worker_dispatch
from llmtool import opencode_direct_developer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import d6_worktree_lifecycle  # noqa: E402
import review_trust_fixture  # noqa: E402


def setUpModule(): review_trust_fixture.start_module()
def tearDownModule(): review_trust_fixture.stop_module()


class FakeClient:
    def __init__(self, reply=None, error=None, replies=None):
        self.reply, self.error, self.calls = reply, error, []
        self.replies = list(replies or [])

    def complete(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        if self.replies:
            return self.replies.pop(0)
        return self.reply


def pm_config(profile, require_configured=True):
    return {'active_provider': 'selected', 'providers': {'selected': {
        'type': 'openai-compatible', 'model': 'pm-model', 'timeout_seconds': 10,
        'provider_options': {}}}, 'generation': {'temperature': 0, 'max_tokens': 64}}


class TaskCompletionTests(unittest.TestCase):
    def setUp(self):
        review_trust_fixture.install_for_case(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        policy = json.loads(Path(worker_dispatch._POLICY_PATH).read_text(encoding='utf-8'))
        policy['workers']['opencode-developer']['dispatch'] = 'ALLOWED'
        policy['workers']['opencode-developer']['reason'] = 'test fixture allows isolated callback checks'
        self.policy_path = self.base / 'worker-dispatch.json'
        self.policy_path.write_text(json.dumps(policy), encoding='utf-8')
        policy_patch = patch.object(worker_dispatch, '_POLICY_PATH', self.policy_path)
        policy_patch.start()
        self.addCleanup(policy_patch.stop)
        self.root = self.base / 'executor'
        self.root.mkdir(mode=0o700)
        self._git('init', '-b', 'feat/executor')
        self._git('config', 'user.name', 'Executor')
        self._git('config', 'user.email', 'executor@example.invalid')
        self.source_path = 'tools/llmtool/tests/test_fixture.py'
        source = self.root / self.source_path
        source.parent.mkdir(parents=True)
        (self.root / 'tools' / 'llmtool' / '.gitignore').write_text(
            '/.d6-bootstrap-report.json\n')
        source.write_text('import unittest\nclass Fixture(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n')
        (self.root / 'README.md').write_text('Repository fixture.\n')
        self._git('add', self.source_path, 'tools/llmtool/.gitignore', 'README.md')
        self._git('commit', '-m', 'source')
        self.source_sha = self._git('rev-parse', 'HEAD')
        trusted_ref = self._trusted_main_ref()
        trusted_ref.start()
        self.addCleanup(trusted_ref.stop)
        self.common = str(self.root / '.git')
        (Path(self.common) / 'llmtool-worktrees').mkdir(mode=0o700)
        self.task_id = 'executor-task'
        self._write_registration(self.task_id, 'executor-agent', 'Executor D6 session',
                                 self.root, 'feat/executor', self.source_sha, self.source_sha,
                                 native_session_id='native-executor')

    def _review_binding(self, task_id='1' * 32, run_id='2' * 32,
                        contract_digest='3' * 64, head=None):
        return (f'LLMTOOL-REVIEW-BINDING v1 task={task_id} run={run_id} '
                f'contract={contract_digest} head={head}')

    def _github_kwargs(self, result_sha, **overrides):
        kwargs = {'pr_number': 10, 'result_sha': result_sha,
                  'task_id': '1' * 32, 'run_id': '2' * 32,
                  'contract_digest': '3' * 64,
                  'executor_identity': 'executor-agent',
                  'executor_session_ref': 'Executor D6 session',
                  'executor_native_session_id': 'native-executor',
                  'reviewer_task_id': 'reviewer-task', 'common_dir': self.common,
                  'task_record': {'developer': {'provider_profile': 'codex'}}}
        kwargs.update(overrides)
        return kwargs

    def _trusted_main_ref(self):
        def github_get(path):
            if path != 'git/ref/heads/main':
                raise AssertionError(f'unexpected GitHub path: {path}')
            return {'ref': 'refs/heads/main',
                    'object': {'type': 'commit', 'sha': self.source_sha}}

        return patch.object(task_completion, '_github_get', side_effect=github_get)

    def _github_response_sequence(self, result_sha, pr, reviews, checks, *,
                                  workflow=None, runs=None, jobs=None, comments=()):
        workflow = workflow or {'id': 42, 'path': '.github/workflows/llmtool-mac-ci.yml',
                                'name': 'llmtool Mac CI', 'state': 'active'}
        check_runs = checks['check_runs']
        for check in check_runs:
            check.setdefault('app', {'id': 15368, 'slug': 'github-actions',
                                     'name': 'GitHub Actions'})
        by_name = {check['name']: check for check in check_runs}
        jobs = jobs or {'total_count': 1, 'jobs': [
            {'id': index + 10, 'run_id': 100, 'name': name, 'runner_name': 'davidproject-self-action',
             'check_run_url': ('https://api.github.com/repos/davidcatcat/davidcattoolsproject/'
                               'check-runs/' + str(by_name.get(name, {}).get('id', 1000 + index))),
             'status': by_name.get(name, {}).get('status', 'completed'),
             'conclusion': by_name.get(name, {}).get('conclusion', 'success')}
            for index, name in enumerate(('check (macOS 3.13)',))]}
        runs = runs or {'total_count': 1, 'workflow_runs': [{
            'id': 100, 'run_number': 5, 'run_attempt': 1, 'workflow_id': workflow['id'],
            'path': '.github/workflows/llmtool-mac-ci.yml', 'event': 'pull_request',
            'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success',
            'pull_requests': [{'number': 10, 'head': {'sha': result_sha}}]}]}
        linux_workflow = {
            'id': 43, 'path': '.github/workflows/llmtool-linux-ci.yml',
            'name': 'llmtool Linux CI', 'state': 'active'}
        linux_runs = {'total_count': 1, 'workflow_runs': [{
            'id': 200, 'run_number': 8, 'run_attempt': 1, 'workflow_id': 43,
            'path': '.github/workflows/llmtool-linux-ci.yml', 'event': 'pull_request',
            'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success',
            'pull_requests': [{'number': 10, 'head': {'sha': result_sha}}]}]}
        linux_jobs = {'total_count': 1, 'jobs': [{
            'id': 51, 'run_id': 200, 'name': 'check (Linux 3.14)',
            'runner_name': 'ec2-self-runner',
            'check_run_url': ('https://api.github.com/repos/davidcatcat/'
                              'davidcattoolsproject/check-runs/61'),
            'status': 'completed', 'conclusion': 'success'}]}
        linux_checks = {'total_count': 1, 'check_runs': [{
            'id': 61, 'name': 'check (Linux 3.14)', 'head_sha': result_sha,
            'status': 'completed', 'conclusion': 'success',
            'app': {'id': 15368, 'slug': 'github-actions', 'name': 'GitHub Actions'}}]}
        # Keep existing Mac response indexes stable and model the required
        # exact-head Linux check after them.
        return [pr, reviews, list(comments), workflow, runs, jobs, checks,
                linux_workflow, linux_runs, linux_jobs, linux_checks]

    def _git(self, *args):
        return subprocess.check_output(['/usr/bin/git', *args], cwd=self.root,
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def _write_registration(self, task_id, agent, session, root, branch, base, head,
                            **extra):
        root.chmod(0o700)
        root_stat = root.stat()
        value = {'schema_version': 1, 'task_id': task_id,
                 'trusted_agent_id': agent, 'session_ref': session,
                 'root_realpath': str(root),
                 'physical_identity': {'device': root_stat.st_dev,
                                       'inode': root_stat.st_ino, 'uid': root_stat.st_uid,
                                       'mode': oct(stat.S_IMODE(root_stat.st_mode))},
                 'git_common_dir': self.common, 'branch': branch, 'base_sha': base,
                 'head_sha': head, 'state': 'ACTIVE', 'lock_state': 'LOCKED'}
        value.update(extra)
        self._write_env2_marker(value, root, submitted_head=head, current_head=head)
        path = Path(self.common) / 'llmtool-worktrees' / (task_id + '.json')
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return path

    def _write_env2_marker(self, record, root, *, submitted_head, current_head):
        report_path = root / 'tools' / 'llmtool' / '.d6-bootstrap-report.json'
        report_path.parent.mkdir(parents=True, exist_ok=True)
        tooling_sha = (record.get('env2_approved_tooling_sha') or record['base_sha'])
        report = {'schema_version': 1, 'result': 'PASS', 'status': 'env_reused',
            'task_id': record['task_id'], 'root_realpath': str(root),
            'head_sha': current_head, 'tooling_sha': tooling_sha,
            'cache_fingerprint':
                'cpython3.13-darwin-arm64-012345abcdef'}
        report_bytes = (json.dumps(report, sort_keys=True, separators=(',', ':')) + '\n').encode()
        report_path.write_bytes(report_bytes)
        report_path.chmod(0o600)
        record['env2_pass_marker'] = {
            'schema_version': 1, 'result': 'PASS', 'task_id': record['task_id'],
            'root_realpath': str(root), 'submitted_head_sha': submitted_head,
            'current_head_sha': current_head,
            'tooling_sha': tooling_sha,
            'environment_fingerprint': report['cache_fingerprint'],
            'report_sha256': hashlib.sha256(report_bytes).hexdigest()}

    def _make_result(self, *, code='    def test_ok(self): self.assertEqual(1, 1)\n',
                     source_sha=None, refresh_env2_marker=True):
        source_sha = self.source_sha if source_sha is None else source_sha
        path = self.root / self.source_path
        path.write_text('import unittest\nclass Fixture(unittest.TestCase):\n' + code)
        self._git('add', self.source_path)
        self._git('commit', '-m', 'executor result')
        result_sha = self._git('rev-parse', 'HEAD')
        patch_text = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', source_sha, result_sha],
            cwd=self.root, text=True)
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record['head_sha'] = result_sha
        if refresh_env2_marker:
            self._write_env2_marker(record, self.root, submitted_head=result_sha,
                                    current_head=result_sha)
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)
        return result_sha, patch_text

    def _make_path_result(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        self._git('add', path)
        self._git('commit', '-m', 'executor path result')
        result_sha = self._git('rev-parse', 'HEAD')
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record['head_sha'] = result_sha
        self._write_env2_marker(record, self.root, submitted_head=result_sha,
                                current_head=result_sha)
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)
        return result_sha

    def _commit_tree(self, tree, *parents, message='crafted commit'):
        args = ['/usr/bin/git', 'commit-tree', tree]
        for parent in parents:
            args.extend(('-p', parent))
        args.extend(('-m', message))
        return subprocess.check_output(args, cwd=self.root, text=True).strip()

    def _rename_deletion_head(self):
        old = self.source_sha
        guard = self.root / 'guard.txt'
        guard_content = ''.join(f'guard line {index}\n' for index in range(30))
        guard.write_text(guard_content)
        self._git('add', 'guard.txt')
        self._git('commit', '-m', 'add guard file to source')
        self.source_sha = self._git('rev-parse', 'HEAD')
        self._write_registration(self.task_id, 'executor-agent', 'Executor D6 session',
                                 self.root, 'feat/executor', self.source_sha, self.source_sha,
                                 native_session_id='native-executor')

        target = self.root / self.source_path
        target.unlink()
        self._git('add', self.source_path)
        self._git('commit', '-m', 'delete source path')
        deletion = self._git('rev-parse', 'HEAD')
        guard.unlink()
        target.write_text(guard_content)
        self._git('add', 'guard.txt', self.source_path)
        self._git('commit', '-m', 'move guard content to deleted path')
        tree = self._git('rev-parse', 'HEAD^{tree}')
        merge = self._commit_tree(tree, deletion, old, message='merge old ancestor')
        self._git('reset', '--hard', merge)
        target.write_text('import unittest\nclass Fixture(unittest.TestCase):\n'
                          '    def test_ok(self): self.assertTrue(True)\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'rewrite source path')
        return old, self._git('rev-parse', 'HEAD')

    def _out_of_scope_merge_head(self):
        self._git('reset', '--hard', self.source_sha)
        self._git('checkout', '-b', 'scratch-outside')
        outside = self.root / 'outside.txt'
        outside.write_text('Outside the task scope.\n')
        self._git('add', 'outside.txt')
        self._git('commit', '-m', 'out of scope branch')
        tree = self._git('rev-parse', 'HEAD^{tree}')
        root_commit = self._commit_tree(tree, message='parentless outside root')

        self._git('checkout', 'feat/executor')
        self._git('reset', '--hard', self.source_sha)
        self._git('checkout', '-b', 'scratch-developer')
        source = self.root / self.source_path
        source.write_text(source.read_text() + '\n# developer change\n')
        outside.write_text('Outside the task scope.\n')
        self._git('add', self.source_path, 'outside.txt')
        self._git('commit', '-m', 'developer tree')
        developer_commit = self._git('rev-parse', 'HEAD')
        developer_tree = self._git('rev-parse', 'HEAD^{tree}')
        result = self._commit_tree(
            developer_tree, root_commit, developer_commit, message='out of scope merge')
        self._git('checkout', 'feat/executor')
        self._git('reset', '--hard', result)
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record['head_sha'] = result
        self._write_env2_marker(record, self.root, submitted_head=result,
                                current_head=result)
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)
        return result, root_commit, developer_commit

    def _modify_delete_conflict_head(self, conflict_path):
        """Create a trusted-main deletion/developer modification; keep ours."""
        self._git('config', 'merge.renames', 'false')
        self._git('checkout', '-b', 'main-conflict', self.source_sha)
        conflict_file = self.root / conflict_path
        conflict_file.unlink()
        self._git('add', conflict_path)
        self._git('commit', '-m', 'trusted main deletes conflict path')
        trusted_base = self._git('rev-parse', 'HEAD')

        self._git('checkout', 'feat/executor')
        self._git('reset', '--hard', self.source_sha)
        source_file = self.root / self.source_path
        if conflict_path != self.source_path:
            source_file.write_text(
                source_file.read_text() + '\n# scoped developer change\n')
        conflict_content = 'Developer conflict modification.\n'
        conflict_file.write_text(conflict_content)
        self._git('add', self.source_path)
        if conflict_path != self.source_path:
            self._git('add', conflict_path)
        self._git('commit', '-m', 'developer modifies conflict path')
        segment_start = self._git('rev-parse', 'HEAD')
        initial_merge = subprocess.run(
            ['/usr/bin/git', '-c', 'merge.renames=false', '-c', 'diff.renames=false',
             'merge', '--no-ff', 'main-conflict', '-m', 'merge trusted main'],
            cwd=self.root, text=True, capture_output=True)
        self.assertNotEqual(initial_merge.returncode, 0, initial_merge.stderr)
        stages = self._git('ls-files', '-u', '--', conflict_path)
        self.assertEqual(
            {line.split('\t', 1)[0].split()[2] for line in stages.splitlines()},
            {'1', '2'})
        self.assertEqual(conflict_file.read_text(), conflict_content)
        self._git('add', conflict_path)
        self._git('commit', '-m', 'resolve modify/delete by keeping developer file')
        result = self._git('rev-parse', 'HEAD')
        self.assertEqual(self._git('rev-parse', result + '^1'), segment_start)
        self.assertEqual(self._git('rev-parse', result + '^2'), trusted_base)
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record['head_sha'] = result
        self._write_env2_marker(record, self.root, submitted_head=result,
                                current_head=result)
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)
        return result, trusted_base, segment_start

    def _modify_delete_conflict_segment_result(self):
        merge_sha, trusted_base, segment_start = self._modify_delete_conflict_head(
            'README.md')
        self.assertEqual(self._git(
            'diff', '--name-only', segment_start, merge_sha), '')
        self.assertNotIn('README.md',
            task_completion._remerge_diff_paths_raw(
                str(self.root), merge_sha).decode('utf-8').split('\x00'))
        result_sha, artifact = self._make_result(source_sha=segment_start)
        self.assertEqual(self._git(
            'diff', '--name-only', segment_start, result_sha), self.source_path)
        return result_sha, trusted_base, segment_start, artifact

    # shape: (trusted main's README.md rename target, or None to delete it;
    # the Developer's README.md rename target, or None to delete it; the path
    # removed when resolving, or None to keep the tree Git left; conflict
    # paths that the remerge read does not report).
    _RENAME_CONFLICT_SHAPES = {
        'rename-delete-main-renames': (
            'main-name.md', None, None, ('main-name.md',)),
        'rename-delete-developer-renames': (
            None, 'developer-name.md', None, ('developer-name.md',)),
        'rename-rename-keep-both': (
            'main-name.md', 'developer-name.md', None,
            ('README.md', 'main-name.md', 'developer-name.md')),
        'rename-rename-keep-main': (
            'main-name.md', 'developer-name.md', 'developer-name.md',
            ('README.md', 'main-name.md')),
        'rename-rename-keep-developer': (
            'main-name.md', 'developer-name.md', 'main-name.md',
            ('README.md', 'developer-name.md')),
    }

    def _segment_head(self, result_sha, trusted_base, segment_start, attributed):
        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', '--no-ext-diff', '--no-textconv',
             '--no-renames', segment_start, result_sha], cwd=self.root, text=True)
        return {'result_sha': result_sha, 'trusted_base': trusted_base,
                'segment_start': segment_start, 'artifact': artifact,
                'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
                'attributed': sorted(attributed)}

    def _rename_conflict_head(self, shape):
        """Merge trusted main into the Developer branch with a real rename conflict.

        README.md is the out-of-scope rename source and rename detection is
        on. The segment starts after the Developer's own rename or deletion,
        so the conflict paths are attributed only by the merge reads.
        """
        main_name, developer_name, dropped, hidden = self._RENAME_CONFLICT_SHAPES[shape]
        original = 'README.md'
        self._git('checkout', '-f', 'feat/executor')
        self._git('reset', '--hard', self.source_sha)
        self._write_registration(self.task_id, 'executor-agent', 'Executor D6 session',
                                 self.root, 'feat/executor', self.source_sha, self.source_sha,
                                 native_session_id='native-executor')
        main_branch = 'main-' + shape
        self._git('checkout', '-b', main_branch)
        if main_name is None:
            self._git('rm', original)
        else:
            self._git('mv', original, main_name)
        self._git('commit', '-m', 'trusted main moves README')
        trusted_base = self._git('rev-parse', 'HEAD')

        self._git('checkout', 'feat/executor')
        source_file = self.root / self.source_path
        source_file.write_text(source_file.read_text() + '\n# scoped developer change\n')
        self._git('add', self.source_path)
        if developer_name is None:
            self._git('rm', original)
        else:
            self._git('mv', original, developer_name)
        self._git('commit', '-m', 'developer moves README')
        segment_start = self._git('rev-parse', 'HEAD')

        merge = subprocess.run(
            ['/usr/bin/git', '-c', 'merge.renames=true', '-c', 'diff.renames=true',
             'merge', '--no-ff', main_branch, '-m', 'merge trusted main'],
            cwd=self.root, text=True, capture_output=True)
        self.assertNotEqual(merge.returncode, 0, merge.stdout + merge.stderr)
        stages = self._git('ls-files', '-u')
        conflicts = {line.split('\t', 1)[1] for line in stages.splitlines()}
        self.assertLessEqual(set(hidden), conflicts, stages)
        if dropped is not None:
            (self.root / dropped).unlink()
        self._git('add', '-A')
        self._git('commit', '-m', 'resolve ' + shape)
        merge_sha = self._git('rev-parse', 'HEAD')
        self.assertEqual(self._git('ls-files', '-u'), '')
        self.assertEqual(self._git('rev-parse', merge_sha + '^1'), segment_start)
        self.assertEqual(self._git('rev-parse', merge_sha + '^2'), trusted_base)
        remerge = task_completion._remerge_diff_paths_raw(
            str(self.root), merge_sha).decode('utf-8').split('\x00')
        for path in hidden:
            self.assertNotIn(path, remerge)
        result_sha, _ = self._make_result(source_sha=segment_start)
        return self._segment_head(result_sha, trusted_base, segment_start,
                                  conflicts | {self.source_path})

    def _trusted_blob_conflict_head(self):
        """Resolve an out-of-scope README content conflict to trusted main's blob."""
        readme = self.root / 'README.md'
        self._git('checkout', '-b', 'main-content', self.source_sha)
        readme.write_text('Trusted main README.\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'trusted main edits README')
        trusted_base = self._git('rev-parse', 'HEAD')

        self._git('checkout', 'feat/executor')
        source_file = self.root / self.source_path
        source_file.write_text(source_file.read_text() + '\n# scoped developer change\n')
        readme.write_text('Developer README.\n')
        self._git('add', self.source_path, 'README.md')
        self._git('commit', '-m', 'developer edits README')
        segment_start = self._git('rev-parse', 'HEAD')

        merge = subprocess.run(
            ['/usr/bin/git', 'merge', '--no-ff', 'main-content', '-m', 'merge trusted main'],
            cwd=self.root, text=True, capture_output=True)
        self.assertNotEqual(merge.returncode, 0, merge.stdout + merge.stderr)
        stages = self._git('ls-files', '-u', '--', 'README.md')
        self.assertEqual(
            {line.split('\t', 1)[0].split()[2] for line in stages.splitlines()},
            {'1', '2', '3'})
        readme.write_text('Trusted main README.\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'resolve README to trusted main')
        merge_sha = self._git('rev-parse', 'HEAD')
        self.assertEqual(self._git('rev-parse', merge_sha + '^1'), segment_start)
        self.assertEqual(self._git('rev-parse', merge_sha + '^2'), trusted_base)
        self.assertEqual(self._git('rev-parse', merge_sha + ':README.md'),
                         self._git('rev-parse', trusted_base + ':README.md'))
        result_sha, _ = self._make_result(source_sha=segment_start)
        return self._segment_head(result_sha, trusted_base, segment_start,
                                  {'README.md', self.source_path})

    def _segment_collector_kwargs(self, head, write_paths):
        return {
            'task_id': self.task_id, 'expected_common_dir': self.common,
            'expected_root': str(self.root), 'expected_branch': 'feat/executor',
            'source_sha': head['segment_start'],
            'task_binding': {'task_id': '1' * 32, 'run_id': '2' * 32,
                             'contract_digest': '3' * 64},
            'expected_agent': 'executor-agent',
            'expected_session_ref': 'Executor D6 session',
            'expected_native_session_id': 'native-executor',
            'write_paths': list(write_paths),
            'trusted_base_sha': head['trusted_base'],
        }

    def _complete_company_gw_task(self, store, task, *, trusted_base, tested_head):
        """Run task complete on a bound company-gw Task with the real collector."""
        real_collector = task_completion.collect_executor_result

        def github_get(path):
            self.assertEqual(path, 'git/ref/heads/main')
            return {'ref': 'refs/heads/main',
                    'object': {'type': 'commit', 'sha': trusted_base}}

        with patch.object(task_completion, '_github_get', side_effect=github_get), \
             patch.object(task_completion, 'collect_executor_result',
                          wraps=real_collector) as collect, \
             patch.object(task_completion, 'run_bounded_tests',
                          return_value=[{
                              'path': self.source_path, 'source_sha': tested_head,
                              'exit_code': 0, 'output_sha256': '6' * 64}]) as tests, \
             patch('llmtool.task_submission_cli._remote_receipt_test_policy',
                   return_value='LOCAL'), \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   side_effect=AssertionError('unexpected provider')) as resolve, \
             patch.object(task_completion, 'collect_github_evidence',
                          side_effect=AssertionError('unexpected review/CI')) as github, \
             redirect_stdout(StringIO()):
            code = run_task_complete(Namespace(
                task_id=task['task_id'], executor_task=self.task_id,
                reviewer_task=None, pr=None), store=store)
        collect.assert_called_once()
        self.assertEqual(collect.call_args.kwargs['trusted_base_sha'], trusted_base)
        self.assertEqual(collect.call_args.kwargs['expected_native_session_id'],
                         'native-executor')
        resolve.assert_not_called()
        github.assert_not_called()
        return code, tests, collect, store.get(task['task_id'])['completion']

    def _collect_result(self, result_sha, artifact, *, trusted_base_sha=None):
        return task_completion.collect_executor_result(
            task_id=self.task_id, expected_common_dir=self.common,
            expected_root=str(self.root), expected_branch='feat/executor',
            source_sha=self.source_sha,
            task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                          'contract_digest': '3' * 64},
            expected_agent='executor-agent', expected_session_ref='Executor D6 session',
            expected_native_session_id='native-executor', write_paths=[self.source_path],
            artifact=artifact,
            artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest(),
            **({'trusted_base_sha': trusted_base_sha}
               if trusted_base_sha is not None else {}))

    def _collect_registered_result(self, result_sha, *, trusted_base_sha=None):
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record['head_sha'] = result_sha
        self._write_env2_marker(record, self.root, submitted_head=result_sha,
                                current_head=result_sha)
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)
        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', self.source_sha, result_sha],
            cwd=self.root, text=True)
        return self._collect_result(result_sha, artifact,
                                    trusted_base_sha=trusted_base_sha)

    def test_claude_result_collector_binds_head_and_env2(self):
        result_sha, _ = self._make_result()
        kwargs = {
            'task_id': self.task_id, 'expected_common_dir': self.common,
            'expected_root': str(self.root), 'expected_branch': 'feat/executor',
            'source_sha': self.source_sha,
            'task_binding': {'task_id': '1' * 32, 'run_id': '2' * 32,
                             'contract_digest': '3' * 64},
            'expected_agent': 'executor-agent',
            'expected_session_ref': 'Executor D6 session',
            'expected_native_session_id': 'native-executor',
            'write_paths': [self.source_path],
            'claude_result': {'executor_task_id': self.task_id, 'head_sha': result_sha},
            'trusted_base_sha': self.source_sha,
        }
        evidence = task_completion.collect_claude_result(**kwargs)
        self.assertEqual(evidence['result_head'], result_sha)
        self.assertEqual(evidence['executor_identity'], 'executor-agent')
        wrong_head = dict(kwargs, claude_result={
            'executor_task_id': self.task_id, 'head_sha': '0' * 40})
        with self.assertRaises(LLMToolError):
            task_completion.collect_claude_result(**wrong_head)

        stale_sha, _ = self._make_result(
            code='    def test_ok(self): self.assertEqual(2, 2)\n',
            refresh_env2_marker=False)
        stale = dict(kwargs, claude_result={
            'executor_task_id': self.task_id, 'head_sha': stale_sha})
        with self.assertRaises(LLMToolError):
            task_completion.collect_claude_result(**stale)

    def test_main_merge_changes_are_excluded_from_developer_scope(self):
        self._git('branch', 'main', self.source_sha)
        self._git('checkout', 'main')
        (self.root / 'README.md').write_text('Updated by main.\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'main documentation update')
        main_sha = self._git('rev-parse', 'HEAD')
        self._git('checkout', 'feat/executor')
        target = self.root / self.source_path
        target.write_text(target.read_text() + '\n# developer change\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'developer change')
        self._git('merge', '--no-ff', 'main', '-m', 'merge main')
        target.write_text(target.read_text() + '# later developer change\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'later developer change')

        result_sha = self._git('rev-parse', 'HEAD')
        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', self.source_sha, result_sha],
            cwd=self.root, text=True)
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record['head_sha'] = result_sha
        self._write_env2_marker(record, self.root, submitted_head=result_sha,
                                current_head=result_sha)
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)

        result = self._collect_result(result_sha, artifact, trusted_base_sha=main_sha)
        self.assertEqual(result['changed_paths'], ['README.md', self.source_path])
        self.assertEqual(result['developer_changed_paths'], [self.source_path])

    def test_fake_main_aliases_cannot_hide_malicious_merge(self):
        self._git('branch', 'legitimate-pin', self.source_sha)
        self._git('checkout', 'legitimate-pin')
        (self.root / 'README.md').write_text('Legitimate pinned main.\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'legitimate pinned main')
        trusted_base_sha = self._git('rev-parse', 'HEAD')
        self._git('checkout', 'feat/executor')
        target = self.root / self.source_path
        target.write_text(target.read_text() + '\nDeveloper change.\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'scoped developer change')
        self._git('branch', 'scoped-developer-change', 'HEAD')
        outside = self.root / 'out.txt'
        outside.write_text('Malicious imported content.\n')
        self._git('add', 'out.txt')
        self._git('commit', '-m', 'malicious out of scope change')
        evil = self._git('rev-parse', 'HEAD')
        self._git('branch', 'malicious-import', evil)

        aliases = ('refs/heads/main', 'refs/heads/master', 'refs/remotes/origin/main')
        for alias in aliases:
            with self.subTest(alias=alias):
                for candidate in aliases:
                    self._git('update-ref', '-d', candidate)
                self._git('update-ref', alias, evil)
                self._git('reset', '--hard', 'scoped-developer-change')
                self._git('merge', '--no-ff', 'malicious-import', '-m',
                          'merge malicious branch')
                result_sha = self._git('rev-parse', 'HEAD')
                with self.assertRaises(LLMToolError) as raised:
                    self._collect_registered_result(
                        result_sha, trusted_base_sha=trusted_base_sha)
                self.assertEqual(raised.exception.completion_code, 'RESULT_SCOPE_MISMATCH')
                self._git('reset', '--hard', 'scoped-developer-change')

    def test_repair_chain_segment_uses_developer_path_classification(self):
        self._git('branch', 'main', self.source_sha)
        self._git('checkout', 'main')
        (self.root / 'README.md').write_text('Main update.\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'main update')
        trusted_base_sha = self._git('rev-parse', 'HEAD')
        self._git('checkout', 'feat/executor')
        segment_start = self.source_sha
        self._git('merge', '--no-ff', 'main', '-m', 'merge main')
        target = self.root / self.source_path
        target.write_text(target.read_text() + '\n# repair segment\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'repair segment')
        segment_end = self._git('rev-parse', 'HEAD')

        paths = task_completion._developer_changed_paths(
            str(self.root), segment_start, segment_end, trusted_base_sha)
        self.assertEqual(paths, (self.source_path,))

    def test_old_ancestor_second_parent_does_not_hide_changes(self):
        old_parent = self.source_sha
        (self.root / 'out.txt').write_text('Developer content.\n')
        self._git('add', 'out.txt')
        self._git('commit', '-m', 'out of scope before merge')
        first_parent = self._git('rev-parse', 'HEAD')
        tree = self._git('rev-parse', first_parent + '^{tree}')
        result_sha = self._git('commit-tree', tree, '-p', first_parent,
                               '-p', old_parent, '-m', 'merge old ancestor')

        paths = task_completion._developer_changed_paths(
            str(self.root), self.source_sha, result_sha, old_parent)
        self.assertIn('out.txt', paths)

    def test_rename_detection_cannot_hide_deleted_source_path(self):
        old, head = self._rename_deletion_head()
        for setting in ('true', 'false', 'copies'):
            with self.subTest(diff_renames=setting):
                self._git('config', 'diff.renames', setting)
                self.assertEqual(task_completion._developer_changed_paths(
                    str(self.root), self.source_sha, head, self.source_sha),
                    ('guard.txt', self.source_path))

        with self.assertRaises(LLMToolError) as raised:
            self._collect_registered_result(head, trusted_base_sha=self.source_sha)
        self.assertEqual(raised.exception.completion_code, 'RESULT_SCOPE_MISMATCH')

        kwargs = {
            'task_id': self.task_id, 'expected_common_dir': self.common,
            'expected_root': str(self.root), 'expected_branch': 'feat/executor',
            'source_sha': self.source_sha,
            'task_binding': {'task_id': '1' * 32, 'run_id': '2' * 32,
                             'contract_digest': '3' * 64},
            'expected_agent': 'executor-agent',
            'expected_session_ref': 'Executor D6 session',
            'expected_native_session_id': 'native-executor',
            'write_paths': [self.source_path],
            'claude_result': {'executor_task_id': self.task_id, 'head_sha': head},
            'trusted_base_sha': self.source_sha,
        }
        with self.assertRaises(LLMToolError) as raised:
            task_completion.collect_claude_result(**kwargs)
        self.assertEqual(raised.exception.completion_code, 'RESULT_SCOPE_MISMATCH')

        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', self.source_sha, head],
            cwd=self.root, text=True)
        with self.assertRaises(LLMToolError) as raised:
            task_completion.collect_executor_result(
                task_id=self.task_id, expected_common_dir=self.common,
                expected_root=str(self.root), expected_branch='feat/executor',
                source_sha=self.source_sha,
                task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                              'contract_digest': '3' * 64},
                expected_agent='executor-agent',
                expected_session_ref='Executor D6 session',
                expected_native_session_id='native-executor',
                write_paths=[self.source_path], artifact=artifact,
                artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest(),
                artifact_segments=[(artifact, hashlib.sha256(artifact.encode()).hexdigest(),
                                    self.source_sha, head)],
                trusted_base_sha=self.source_sha)
        self.assertEqual(raised.exception.completion_code,
                         'DEVELOPER_ARTIFACT_CHAIN_INVALID')

    def test_octopus_trusted_second_parent_is_refused_without_remerge_read(self):
        old = self.source_sha
        guard = self.root / 'guard.txt'
        guard.write_text(''.join(f'guard line {index}\n' for index in range(30)))
        self._git('add', 'guard.txt')
        self._git('commit', '-m', 'add guard source')
        source = self._git('rev-parse', 'HEAD')
        target = self.root / self.source_path
        target.write_text(target.read_text() + '\n# scoped change\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'in-scope developer change')
        developer = self._git('rev-parse', 'HEAD')
        tree = self._git('rev-parse', 'HEAD^{tree}')
        side = self._commit_tree(tree, source, message='side branch')
        octopus = self._commit_tree(tree, developer, old, side, message='octopus')

        with patch.object(task_completion, '_remerge_diff_paths_raw') as remerge:
            with self.assertRaises(LLMToolError) as raised:
                task_completion._developer_changed_paths(
                    str(self.root), source, octopus, source)
        self.assertEqual(raised.exception.completion_code, 'RESULT_DIFF_INVALID')
        remerge.assert_not_called()

    def test_trusted_main_history_may_contain_octopus_merge(self):
        source = self.source_sha
        self._git('checkout', '-b', 'main', source)
        for branch, path in (('main-side-one', 'main-one.txt'),
                             ('main-side-two', 'main-two.txt')):
            self._git('checkout', '-b', branch, source)
            (self.root / path).write_text(path + '\n')
            self._git('add', path)
            self._git('commit', '-m', branch)
        self._git('checkout', 'main')
        self._git('merge', '--no-ff', '-m', 'octopus main merge',
                  'main-side-one', 'main-side-two')
        trusted = self._git('rev-parse', 'HEAD')
        self._git('checkout', 'feat/executor')
        target = self.root / self.source_path
        target.write_text(target.read_text() + '\n# developer change\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'developer change')
        self._git('merge', '--no-ff', 'main', '-m', 'merge octopus main history')
        head = self._git('rev-parse', 'HEAD')
        self.assertEqual(task_completion._developer_changed_paths(
            str(self.root), source, head, trusted), (self.source_path,))

    def test_equal_blob_with_old_mode_is_developer_changed_path(self):
        guard = self.root / 'guard.sh'
        guard.write_text('#!/bin/sh\necho guard\n')
        guard.chmod(0o755)
        self._git('add', 'guard.sh')
        self._git('commit', '-m', 'add executable guard as old commit')
        old = self._git('rev-parse', 'HEAD')
        guard.chmod(0o644)
        self._git('add', 'guard.sh')
        self._git('commit', '-m', 'source changes guard mode')
        source = self._git('rev-parse', 'HEAD')
        target = self.root / self.source_path
        target.write_text(target.read_text() + '\n# scoped change\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'in-scope developer change')
        developer = self._git('rev-parse', 'HEAD')
        guard.chmod(0o755)
        self._git('add', 'guard.sh')
        tree = self._git('write-tree')
        merge = self._commit_tree(tree, developer, old, message='restore old mode')
        self.assertIn('guard.sh', task_completion._developer_changed_paths(
            str(self.root), source, merge, source))

    def test_out_of_scope_merge_with_unrelated_first_parent_is_refused_by_collectors(self):
        result_sha, _, _ = self._out_of_scope_merge_head()
        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', self.source_sha, result_sha],
            cwd=self.root, text=True)
        with self.assertRaises(LLMToolError) as raised:
            self._collect_result(result_sha, artifact)
        self.assertEqual(raised.exception.completion_code, 'SOURCE_NOT_ANCESTOR')

        kwargs = {
            'task_id': self.task_id, 'expected_common_dir': self.common,
            'expected_root': str(self.root), 'expected_branch': 'feat/executor',
            'source_sha': self.source_sha,
            'task_binding': {'task_id': '1' * 32, 'run_id': '2' * 32,
                             'contract_digest': '3' * 64},
            'expected_agent': 'executor-agent',
            'expected_session_ref': 'Executor D6 session',
            'expected_native_session_id': 'native-executor',
            'write_paths': [self.source_path],
            'claude_result': {'executor_task_id': self.task_id, 'head_sha': result_sha},
            'trusted_base_sha': self.source_sha,
        }
        with self.assertRaises(LLMToolError) as raised:
            task_completion.collect_claude_result(**kwargs)
        self.assertEqual(raised.exception.completion_code, 'SOURCE_NOT_ANCESTOR')

    def test_undoing_source_change_through_second_parent_is_refused(self):
        readme = self.root / 'README.md'
        readme.write_text('Changed source README.\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'advance source')
        new_source = self._git('rev-parse', 'HEAD')
        parent_of_source = self._git('rev-parse', new_source + '^')
        developer = self.root / self.source_path
        developer.write_text(developer.read_text() + '\n# developer change\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'developer change')
        developer_commit = self._git('rev-parse', 'HEAD')
        tree = self._git('rev-parse', 'HEAD^{tree}')
        old_readme = subprocess.check_output(
            ['/usr/bin/git', 'show', self.source_sha + ':README.md'],
            cwd=self.root, text=True)
        readme.write_text(old_readme)
        self._git('add', 'README.md')
        self._git('commit', '-m', 'restore old README')
        restored_tree = self._git('rev-parse', 'HEAD^{tree}')
        result = self._commit_tree(restored_tree, parent_of_source, developer_commit,
                                   message='undo source via first parent')
        self._git('reset', '--hard', result)
        self.source_sha = new_source
        self._write_registration(self.task_id, 'executor-agent', 'Executor D6 session',
                                 self.root, 'feat/executor', new_source, result,
                                 native_session_id='native-executor')
        with self.assertRaises(LLMToolError) as raised:
            self._collect_registered_result(result)
        self.assertEqual(raised.exception.completion_code, 'SOURCE_NOT_ANCESTOR')

    def test_first_parent_merge_from_main_and_developer_side_branch_pass(self):
        self._git('branch', 'main', self.source_sha)
        self._git('checkout', 'main')
        readme = self.root / 'README.md'
        readme.write_text('Main branch update.\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'advance main README')
        trusted_base_sha = self._git('rev-parse', 'HEAD')

        self._git('checkout', '-b', 'developer-side')
        side_path = 'side.txt'
        (self.root / side_path).write_text('Side branch change.\n')
        self._git('add', side_path)
        self._git('commit', '-m', 'side branch change')
        side_tip = self._git('rev-parse', 'HEAD')
        self._git('checkout', 'feat/executor')
        main_path = self.root / self.source_path
        main_path.write_text(main_path.read_text() + '\n# before merge\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'developer before merge')
        self._git('merge', '--no-ff', 'main', '-m', 'merge main')
        main_merge_tip = self._git('rev-parse', 'HEAD')
        parents = self._git('rev-list', '--parents', '-n', '1', main_merge_tip).split()
        self.assertEqual(len(parents), 3)
        main_path.write_text(main_path.read_text() + '# after main merge\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'developer after merge')
        main_result = self._git('rev-parse', 'HEAD')
        main_paths = task_completion._developer_changed_paths(
            str(self.root), self.source_sha, main_result, trusted_base_sha)
        self.assertIn('README.md', self._git('diff', '--name-only', self.source_sha,
                                             main_result).splitlines())
        self.assertNotIn('README.md', main_paths)
        self.assertEqual(main_paths, (self.source_path,))

        self._git('merge', '--no-ff', side_tip, '-m', 'merge developer side branch')
        side_result = self._git('rev-parse', 'HEAD')
        self.assertEqual(task_completion._developer_changed_paths(
            str(self.root), self.source_sha, side_result, trusted_base_sha),
            (side_path, self.source_path))

    def test_parentless_commits_in_range_or_broken_first_parent_chain_are_refused(self):
        # A parentless commit in the range is refused even when M's first-parent
        # chain reaches source through the Developer commit.
        _, parentless, developer = self._out_of_scope_merge_head()
        developer_tree = self._git('rev-parse', developer + '^{tree}')
        merge = self._commit_tree(developer_tree, developer, parentless,
                                  message='parentless second parent')
        with self.assertRaises(LLMToolError) as raised:
            task_completion._developer_changed_paths(
                str(self.root), self.source_sha, merge, self.source_sha)
        self.assertEqual(raised.exception.completion_code, 'SOURCE_NOT_ANCESTOR')

        # The source is only a second parent; the first parent is unrelated.
        root_tree = self._git('rev-parse', parentless + '^{tree}')
        broken = self._commit_tree(root_tree, parentless, self.source_sha,
                                   message='broken first parent')
        with self.assertRaises(LLMToolError) as raised:
            task_completion._developer_changed_paths(
                str(self.root), self.source_sha, broken, self.source_sha)
        self.assertEqual(raised.exception.completion_code, 'SOURCE_NOT_ANCESTOR')

        # Both commits are on source history, but start is reachable from end
        # only through the second parent; no parentless commit is in the range.
        self._git('checkout', 'feat/executor')
        self._git('reset', '--hard', self.source_sha)
        (self.root / 'README.md').write_text('Source X.\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'source X')
        x_commit = self._git('rev-parse', 'HEAD')
        (self.root / 'README.md').write_text('Source start.\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'source start')
        start = self._git('rev-parse', 'HEAD')
        start_tree = self._git('rev-parse', start + '^{tree}')
        end = self._commit_tree(start_tree, x_commit, start,
                                message='repair segment second parent')
        with self.assertRaises(LLMToolError) as raised:
            task_completion._developer_changed_paths(
                str(self.root), start, end, start)
        self.assertEqual(raised.exception.completion_code, 'SOURCE_NOT_ANCESTOR')

    def test_parentless_head_refused_and_shallow_boundary_is_detected(self):
        tree = self._git('rev-parse', self.source_sha + '^{tree}')
        parentless_head = self._commit_tree(tree, message='parentless head')
        with self.assertRaises(LLMToolError) as raised:
            task_completion._developer_changed_paths(
                str(self.root), self.source_sha, parentless_head, self.source_sha)
        self.assertEqual(raised.exception.completion_code, 'SOURCE_NOT_ANCESTOR')

        (self.root / 'outside.txt').write_text('Outside change.\n')
        self._git('add', 'outside.txt')
        self._git('commit', '-m', 'out of scope E')
        boundary = self._git('rev-parse', 'HEAD')
        target = self.root / self.source_path
        target.write_text(target.read_text() + '\n# in scope X\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'in scope X')
        result = self._git('rev-parse', 'HEAD')
        shallow = Path(self.common) / 'shallow'
        shallow.write_text(boundary + '\n')
        self.addCleanup(lambda: shallow.unlink(missing_ok=True))
        with self.assertRaises(LLMToolError) as raised:
            task_completion._developer_changed_paths(
                str(self.root), self.source_sha, result, self.source_sha)
        self.assertEqual(raised.exception.completion_code, 'SOURCE_NOT_ANCESTOR')
        shallow.unlink()
        self.assertIn('outside.txt', task_completion._developer_changed_paths(
            str(self.root), self.source_sha, result, self.source_sha))

    def test_invalid_trusted_main_pin_is_rejected(self):
        with self.assertRaises(LLMToolError) as raised:
            task_completion._developer_changed_paths(
                str(self.root), self.source_sha, self.source_sha, 'not-a-commit')
        self.assertEqual(raised.exception.completion_code, 'GIT_INSPECTION_FAILED')

    def test_nul_path_output_preserves_quoted_unicode_names(self):
        path = 'folder/name with spaces é.py'
        result_sha = self._make_path_result(path, 'value = 1\n')
        paths = task_completion._developer_changed_paths(
            str(self.root), self.source_sha, result_sha, self.source_sha)
        self.assertEqual(paths, (path,))

    def test_deletions_and_mode_changes_are_attributed(self):
        deleted = 'delete me.txt'
        executable = 'run me.sh'
        for path in (deleted, executable):
            (self.root / path).write_text('content\n')
        self._git('add', deleted, executable)
        self._git('commit', '-m', 'add mode fixtures')
        base = self._git('rev-parse', 'HEAD')
        (self.root / deleted).unlink()
        (self.root / executable).chmod(0o755)
        self._git('add', '-A')
        self._git('commit', '-m', 'delete and change mode')
        result = self._git('rev-parse', 'HEAD')
        self.assertEqual(task_completion._developer_changed_paths(
            str(self.root), base, result, base), (deleted, executable))

    def test_overlapping_main_and_developer_edits_remain_attributed(self):
        self._git('branch', 'main-overlap', self.source_sha)
        self._git('checkout', 'main-overlap')
        readme = self.root / 'README.md'
        readme.write_text('Repository fixture.\nMain line.\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'main overlapping file edit')
        trusted_base_sha = self._git('rev-parse', 'HEAD')
        self._git('checkout', 'feat/executor')
        readme.write_text('Developer line.\nRepository fixture.\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'developer overlapping file edit')
        self._git('merge', '--no-ff', 'main-overlap', '-m', 'merge overlapping main')
        paths = task_completion._developer_changed_paths(
            str(self.root), self.source_sha, self._git('rev-parse', 'HEAD'), trusted_base_sha)
        self.assertIn('README.md', paths)

    def test_out_of_scope_merge_resolution_is_refused(self):
        self._git('branch', 'main', self.source_sha)
        self._git('checkout', 'main')
        fixture = self.root / self.source_path
        fixture.write_text('import unittest\nclass Fixture(unittest.TestCase):\n'
                           '    def test_main(self): self.assertTrue(True)\n')
        (self.root / 'README.md').write_text('Main version.\n')
        self._git('add', self.source_path, 'README.md')
        self._git('commit', '-m', 'main edits')
        trusted_base_sha = self._git('rev-parse', 'HEAD')
        self._git('checkout', 'feat/executor')
        fixture.write_text('import unittest\nclass Fixture(unittest.TestCase):\n'
                           '    def test_developer(self): self.assertTrue(True)\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'scoped developer edit')
        merge = subprocess.run(['/usr/bin/git', 'merge', '--no-ff', 'main', '-m',
                                'merge main'], cwd=self.root, text=True,
                               capture_output=True)
        self.assertNotEqual(merge.returncode, 0)
        fixture.write_text('import unittest\nclass Fixture(unittest.TestCase):\n'
                           '    def test_resolved(self): self.assertTrue(True)\n')
        (self.root / 'README.md').write_text('Developer conflict-resolution edit.\n')
        self._git('add', self.source_path, 'README.md')
        self._git('commit', '-m', 'resolve merge')

        result_sha = self._git('rev-parse', 'HEAD')
        with self.assertRaises(LLMToolError) as raised:
            self._collect_registered_result(result_sha, trusted_base_sha=trusted_base_sha)
        self.assertEqual(raised.exception.completion_code, 'RESULT_SCOPE_MISMATCH')

    def test_modify_delete_conflict_is_attributed_by_both_committed_collectors(self):
        result_sha, trusted_base, segment_start, artifact = (
            self._modify_delete_conflict_segment_result())
        common = {
            'task_id': self.task_id, 'expected_common_dir': self.common,
            'expected_root': str(self.root), 'expected_branch': 'feat/executor',
            'source_sha': segment_start,
            'task_binding': {'task_id': '1' * 32, 'run_id': '2' * 32,
                             'contract_digest': '3' * 64},
            'expected_agent': 'executor-agent',
            'expected_session_ref': 'Executor D6 session',
            'expected_native_session_id': 'native-executor',
            'write_paths': [self.source_path],
            'trusted_base_sha': trusted_base,
        }
        routes = (
            ('executor', task_completion.collect_executor_result, {
                'artifact': artifact,
                'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest()}),
            ('claude', task_completion.collect_claude_result, {
                'claude_result': {
                    'executor_task_id': self.task_id, 'head_sha': result_sha}}),
        )
        for name, collector, extra in routes:
            with self.subTest(collector=name, scope='outside'):
                with self.assertRaises(LLMToolError) as raised:
                    collector(**common, **extra)
                self.assertEqual(raised.exception.completion_code,
                                 'RESULT_SCOPE_MISMATCH')
            with self.subTest(collector=name, scope='inside'):
                result = collector(**{
                    **common, 'write_paths': [self.source_path, 'README.md']},
                    **extra)
                self.assertEqual(result['result_head'], result_sha)
                self.assertEqual(result['changed_paths'], [self.source_path])
                self.assertEqual(result['developer_changed_paths'],
                                 sorted([self.source_path, 'README.md']))

    def test_modify_delete_conflict_is_attributed_in_repair_segment(self):
        result_sha, trusted_base, segment_start, artifact = (
            self._modify_delete_conflict_segment_result())
        self.assertEqual(task_completion._developer_changed_paths(
            str(self.root), segment_start, result_sha, trusted_base),
            tuple(sorted(['README.md', self.source_path])))
        with self.assertRaises(LLMToolError) as raised:
            task_completion.collect_executor_result(
                task_id=self.task_id, expected_common_dir=self.common,
                expected_root=str(self.root), expected_branch='feat/executor',
                source_sha=segment_start,
                task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                              'contract_digest': '3' * 64},
                expected_agent='executor-agent', expected_session_ref='Executor D6 session',
                expected_native_session_id='native-executor', write_paths=[self.source_path],
                artifact=artifact,
                artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest(),
                artifact_segments=[(artifact, hashlib.sha256(artifact.encode()).hexdigest(),
                                    segment_start, result_sha)],
                trusted_base_sha=trusted_base)
        self.assertEqual(raised.exception.completion_code,
                         'DEVELOPER_ARTIFACT_CHAIN_INVALID')

    def test_worktree_modify_delete_conflict_is_refused(self):
        _, _, segment_start = self._modify_delete_conflict_head('README.md')
        self._git('reset', '--hard', segment_start)
        merge = subprocess.run(['/usr/bin/git', 'merge', '--no-ff', 'main-conflict',
                                '-m', 'merge trusted deletion'], cwd=self.root,
                               text=True, capture_output=True)
        self.assertNotEqual(merge.returncode, 0)
        (self.root / 'README.md').unlink(missing_ok=True)
        self._git('add', '-A', 'README.md')

        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record['head_sha'] = segment_start
        self._write_env2_marker(record, self.root, submitted_head=segment_start,
                                current_head=segment_start)
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)
        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', '--no-ext-diff', '--no-textconv',
             '--no-renames', self.source_sha, '--'], cwd=self.root)
        with self.assertRaises(LLMToolError) as raised:
            task_completion.collect_executor_worktree_result(
                task_id=self.task_id, expected_common_dir=self.common,
                expected_root=str(self.root), expected_branch='feat/executor',
                source_sha=self.source_sha, expected_result_head=segment_start,
                task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                              'contract_digest': '3' * 64},
                expected_agent='executor-agent', expected_session_ref='Executor D6 session',
                expected_native_session_id='native-executor',
                read_paths=[self.source_path], write_paths=[self.source_path],
                artifact=artifact.decode('utf-8'),
                artifact_sha256=hashlib.sha256(artifact).hexdigest())
        self.assertEqual(raised.exception.completion_code, 'WORKTREE_SCOPE_MISMATCH')

    def test_task_complete_rejects_out_of_scope_modify_delete_conflict(self):
        result_sha, trusted_base, segment_start, artifact = (
            self._modify_delete_conflict_segment_result())
        real_collector = task_completion.collect_executor_result
        store = TaskStore(self.base / 'modify-delete-task-complete')

        def github_get(path):
            self.assertEqual(path, 'git/ref/heads/main')
            return {'ref': 'refs/heads/main',
                    'object': {'type': 'commit', 'sha': trusted_base}}

        for allow_conflict in (False, True):
            with self.subTest(allow_conflict=allow_conflict):
                task = self._active_pm_task(self._task(), route='company-gw')
                paths = sorted([self.source_path] + (
                    ['README.md'] if allow_conflict else []))
                task['developer'].update(
                    source_sha=segment_start, read_paths=paths, write_paths=paths,
                    artifact=artifact,
                    artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
                task['completion']['evidence'].update(
                    source_sha=segment_start, result_head=segment_start,
                    executor_native_session_id='native-executor',
                    executor_native_session='VERIFIED')
                store.create(task)
                with patch.object(task_completion, '_github_get',
                                  side_effect=github_get), \
                     patch.object(task_completion, 'collect_executor_result',
                                  wraps=real_collector) as collect, \
                     patch.object(task_completion, 'run_bounded_tests',
                                  return_value=[{
                                      'path': self.source_path,
                                      'source_sha': result_sha, 'exit_code': 0,
                                      'output_sha256': '6' * 64}]) as tests, \
                     patch('llmtool.task_submission_cli._remote_receipt_test_policy',
                           return_value='LOCAL'), \
                     patch('llmtool.task_submission_cli.resolve_profile_config',
                           side_effect=AssertionError('unexpected provider')) as resolve, \
                     patch.object(task_completion, 'collect_github_evidence',
                           side_effect=AssertionError('unexpected review/CI')) as github, \
                     redirect_stdout(StringIO()):
                    code = run_task_complete(Namespace(
                        task_id=task['task_id'], executor_task=self.task_id,
                        reviewer_task=None, pr=None), store=store)
                collect.assert_called_once()
                self.assertEqual(collect.call_args.kwargs['source_sha'],
                                 segment_start)
                self.assertEqual(collect.call_args.kwargs['trusted_base_sha'],
                                 trusted_base)
                self.assertEqual(
                    collect.call_args.kwargs['expected_native_session_id'],
                    'native-executor')
                resolve.assert_not_called()
                github.assert_not_called()
                completion = store.get(task['task_id'])['completion']
                self.assertEqual(completion['pm_dispatch'], 'NOT_STARTED')
                if allow_conflict:
                    self.assertEqual(code, 0)
                    tests.assert_called_once()
                    self.assertEqual(completion['state'], 'PM_EVIDENCE_READY')
                    self.assertEqual(completion['missing_gates'], [])
                    result = completion['evidence']['result']
                    self.assertEqual(result['source_sha'], segment_start)
                    self.assertEqual(result['result_head'], result_sha)
                    self.assertEqual(result['developer_changed_paths'],
                                     sorted([self.source_path, 'README.md']))
                else:
                    self.assertEqual(code, 1)
                    tests.assert_not_called()
                    self.assertEqual(completion['state'], 'WAITING_EXECUTOR')
                    self.assertEqual(completion['missing_gates'],
                                     ['RESULT_SCOPE_MISMATCH'])

    def test_in_scope_modify_delete_conflict_passes_scope_collection(self):
        result_sha, trusted_base, _ = self._modify_delete_conflict_head(self.source_path)
        result = self._collect_registered_result(result_sha, trusted_base_sha=trusted_base)
        self.assertIn(self.source_path, result['developer_changed_paths'])

    def test_repo_merge_driver_cannot_hide_conflict_or_run(self):
        head = self._trusted_blob_conflict_head()
        args = (str(self.root), head['segment_start'], head['result_sha'],
                head['trusted_base'])
        marker = self.base / 'merge-driver-ran'
        self._git('config', 'merge.z.driver',
                  'touch ' + shlex.quote(str(marker)) + ' && cp %B %A')
        attributes = Path(self.common) / 'info' / 'attributes'
        attributes.parent.mkdir(parents=True, exist_ok=True)
        for route in ('merge.default', 'info/attributes'):
            with self.subTest(route=route):
                if route == 'merge.default':
                    self._git('config', 'merge.default', 'z')
                else:
                    self._git('config', '--unset', 'merge.default')
                    attributes.write_text('README.md merge=z\n')
                # Control: a merge read in the repository runs the driver and
                # resolves README.md cleanly to trusted main, hiding it.
                probe = subprocess.run(
                    ['/usr/bin/git', 'merge-tree', '--write-tree', '--name-only',
                     head['segment_start'], head['trusted_base']],
                    cwd=self.root, text=True, capture_output=True)
                self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
                self.assertTrue(marker.exists())
                marker.unlink()

                self.assertIn('README.md', task_completion._developer_changed_paths(*args))
                with self.assertRaises(LLMToolError) as raised:
                    task_completion.collect_executor_result(
                        **self._segment_collector_kwargs(head, [self.source_path]),
                        artifact=head['artifact'],
                        artifact_sha256=head['artifact_sha256'])
                self.assertEqual(raised.exception.completion_code,
                                 'RESULT_SCOPE_MISMATCH')
                self.assertFalse(marker.exists())

    def test_repo_merge_renames_config_does_not_change_rename_delete_result(self):
        head = self._rename_conflict_head('rename-delete-main-renames')
        args = (str(self.root), head['segment_start'], head['result_sha'],
                head['trusted_base'])
        expected = tuple(head['attributed'])
        self.assertIn('main-name.md', expected)
        self.assertEqual(task_completion._developer_changed_paths(*args), expected)
        self._git('config', 'merge.renames', 'false')
        # No in-repository control: whether a merge read there honours
        # merge.renames depends on the Git version. The private reads must
        # give the same result either way.
        self.assertEqual(task_completion._developer_changed_paths(*args), expected)
        with self.assertRaises(LLMToolError) as raised:
            task_completion.collect_executor_result(
                **self._segment_collector_kwargs(head, [self.source_path]),
                artifact=head['artifact'], artifact_sha256=head['artifact_sha256'])
        self.assertEqual(raised.exception.completion_code, 'RESULT_SCOPE_MISMATCH')

    def test_private_merge_read_failure_stops_inspection(self):
        root, missing = str(self.root), '1' * 40
        reads = {
            'remerge': lambda commit: task_completion._remerge_diff_paths_raw(root, commit),
            'merge-tree': lambda commit: task_completion._merge_tree_conflict_paths_raw(
                root, commit, self.source_sha),
        }
        for name, read in reads.items():
            with self.subTest(read=name, failure='missing-object'):
                with self.assertRaises(LLMToolError) as raised:
                    read(missing)
                self.assertEqual(raised.exception.completion_code,
                                 'GIT_INSPECTION_FAILED')
            with self.subTest(read=name, failure='temporary-repository'):
                with patch.object(task_completion.tempfile, 'TemporaryDirectory',
                                  side_effect=OSError('no temporary directory')):
                    with self.assertRaises(LLMToolError) as raised:
                        read(self.source_sha)
                self.assertEqual(raised.exception.completion_code,
                                 'GIT_INSPECTION_FAILED')

    def _classified_git_failure(self, code, stage, seen):
        """Make the real _git stop with code at one stage, recording what it raised."""
        real_git = task_completion._git

        def run(root, args, **options):
            if ('env_overrides' in options) != (stage == 'merge-read'):
                return real_git(root, args, **options)
            failure = nullcontext()
            if code == 'GIT_INSPECTION_TIMEOUT':
                failure = patch.object(task_completion, '_TIMEOUT', 0)
            elif code == 'GIT_INSPECTION_UNAVAILABLE':
                failure = patch.object(task_completion.subprocess, 'Popen',
                                       side_effect=OSError('no git'))
            else:
                options['limit'] = 0
            try:
                with failure:
                    return real_git(root, args, **options)
            except LLMToolError as error:
                seen.append(error.completion_code)
                raise

        return patch.object(task_completion, '_git', run)

    def test_private_merge_read_classified_git_failure_stops_inspection(self):
        # A non-merge commit, so both reads print output for the size limit.
        commit, _ = self._make_result()
        root = str(self.root)
        reads = {
            'remerge': lambda: task_completion._remerge_diff_paths_raw(root, commit),
            'merge-tree': lambda: task_completion._merge_tree_conflict_paths_raw(
                root, commit, self.source_sha),
        }
        for name, read in reads.items():
            for stage in ('objects-path', 'merge-read'):
                for code in ('GIT_INSPECTION_TIMEOUT', 'GIT_INSPECTION_UNAVAILABLE',
                             'GIT_EVIDENCE_TOO_LARGE'):
                    with self.subTest(read=name, stage=stage, code=code):
                        seen = []
                        with self._classified_git_failure(code, stage, seen):
                            with self.assertRaises(LLMToolError) as raised:
                                read()
                        self.assertEqual(seen, [code])
                        self.assertEqual(raised.exception.completion_code,
                                         'GIT_INSPECTION_FAILED')

    def _assert_rename_conflict_collectors(self, head):
        executor = {'artifact': head['artifact'],
                    'artifact_sha256': head['artifact_sha256']}
        routes = (
            ('executor', task_completion.collect_executor_result, executor,
             'RESULT_SCOPE_MISMATCH'),
            ('claude', task_completion.collect_claude_result, {
                'claude_result': {'executor_task_id': self.task_id,
                                  'head_sha': head['result_sha']}},
             'RESULT_SCOPE_MISMATCH'),
            ('repair-segment', task_completion.collect_executor_result, {
                **executor, 'artifact_segments': [(
                    head['artifact'], head['artifact_sha256'],
                    head['segment_start'], head['result_sha'])]},
             'DEVELOPER_ARTIFACT_CHAIN_INVALID'),
        )
        for name, collector, extra, code in routes:
            with self.subTest(route=name, scope='outside'):
                with self.assertRaises(LLMToolError) as raised:
                    collector(**self._segment_collector_kwargs(
                        head, [self.source_path]), **extra)
                self.assertEqual(raised.exception.completion_code, code)
            with self.subTest(route=name, scope='inside'):
                result = collector(**self._segment_collector_kwargs(
                    head, head['attributed']), **extra)
                self.assertEqual(result['result_head'], head['result_sha'])
                self.assertEqual(result['developer_changed_paths'], head['attributed'])

    def _assert_rename_conflict_task_complete(self, head, shape):
        store = TaskStore(self.base / ('rename-conflict-task-complete-' + shape))
        for in_scope in (False, True):
            with self.subTest(route='task-complete', in_scope=in_scope):
                task = self._active_pm_task(self._task(), route='company-gw')
                paths = list(head['attributed']) if in_scope else [self.source_path]
                task['developer'].update(
                    source_sha=head['segment_start'], read_paths=paths,
                    write_paths=paths, artifact=head['artifact'],
                    artifact_sha256=head['artifact_sha256'])
                task['completion']['evidence'].update(
                    source_sha=head['segment_start'],
                    result_head=head['segment_start'],
                    executor_native_session_id='native-executor',
                    executor_native_session='VERIFIED')
                store.create(task)
                code, tests, collect, completion = self._complete_company_gw_task(
                    store, task, trusted_base=head['trusted_base'],
                    tested_head=head['result_sha'])
                self.assertEqual(collect.call_args.kwargs['source_sha'],
                                 head['segment_start'])
                self.assertEqual(completion['pm_dispatch'], 'NOT_STARTED')
                if in_scope:
                    self.assertEqual(code, 0)
                    tests.assert_called_once()
                    # task complete hands the authenticated main pin to the tests.
                    self.assertEqual(tests.call_args.kwargs['trusted_base_sha'],
                                     head['trusted_base'])
                    self.assertEqual(completion['state'], 'PM_EVIDENCE_READY')
                    self.assertEqual(completion['missing_gates'], [])
                    result = completion['evidence']['result']
                    self.assertEqual(result['source_sha'], head['segment_start'])
                    self.assertEqual(result['result_head'], head['result_sha'])
                    self.assertEqual(result['developer_changed_paths'],
                                     head['attributed'])
                else:
                    self.assertEqual(code, 1)
                    tests.assert_not_called()
                    self.assertEqual(completion['state'], 'WAITING_EXECUTOR')
                    self.assertEqual(completion['missing_gates'],
                                     ['RESULT_SCOPE_MISMATCH'])

    def test_rename_conflicts_are_attributed_on_every_completion_route(self):
        for shape in self._RENAME_CONFLICT_SHAPES:
            with self.subTest(shape=shape):
                head = self._rename_conflict_head(shape)
                self._assert_rename_conflict_collectors(head)
                self._assert_rename_conflict_task_complete(head, shape)

    def test_task_complete_executor_route_waits_on_head_moved_to_out_of_scope_merge(self):
        store = TaskStore(self.base / 'moved-head-task-complete')
        task = self._active_pm_task(self._task(), route='company-gw')
        task['completion']['evidence'].update(
            executor_native_session_id='native-executor',
            executor_native_session='VERIFIED')
        store.create(task)
        result_sha, _, _ = self._out_of_scope_merge_head()
        code, tests, collect, completion = self._complete_company_gw_task(
            store, task, trusted_base=self.source_sha, tested_head=result_sha)
        self.assertEqual(collect.call_args.kwargs['source_sha'], self.source_sha)
        self.assertEqual(code, 1)
        tests.assert_not_called()
        self.assertEqual(completion['state'], 'WAITING_EXECUTOR')
        self.assertEqual(completion['missing_gates'], ['SOURCE_NOT_ANCESTOR'])
        self.assertEqual(completion['pm_dispatch'], 'NOT_STARTED')

    def _register_head(self, head):
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record['head_sha'] = head
        self._write_env2_marker(record, self.root, submitted_head=head, current_head=head)
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)
        return head

    def _move_main(self, path, text):
        """Commit one trusted-main change, then return to the Developer branch."""
        if not self._git('branch', '--list', 'main'):
            self._git('branch', 'main', self.source_sha)
        self._git('checkout', 'main')
        (self.root / path).write_text(text)
        self._git('add', path)
        self._git('commit', '-m', 'trusted main changes ' + path)
        trusted = self._git('rev-parse', 'HEAD')
        self._git('checkout', 'feat/executor')
        return trusted

    def _developer_commit(self, path=None, text='# developer change\n'):
        path = self.source_path if path is None else path
        target = self.root / path
        target.write_text((target.read_text() if target.exists() else '') + text)
        self._git('add', path)
        self._git('commit', '-m', 'developer changes ' + path)
        return self._git('rev-parse', 'HEAD')

    def _merge_main(self, *options):
        self._git('merge', '--no-ff', *options, '-m', 'merge main', 'main')
        return self._register_head(self._git('rev-parse', 'HEAD'))

    def _merge_main_with_change(self, path):
        """Merge main cleanly, then hide one more change inside the merge commit."""
        self._git('merge', '--no-ff', '--no-commit', 'main')
        (self.root / path).write_text('Hidden in the merge.\n')
        self._git('add', path)
        self._git('commit', '-m', 'merge main with a hidden change')
        return self._register_head(self._git('rev-parse', 'HEAD'))

    def _artifact(self, start, end):
        return subprocess.check_output(['/usr/bin/git', 'diff', '--binary', start, end],
                                       cwd=self.root, text=True)

    def _artifact_segment(self, start, tip, end):
        artifact = self._artifact(start, tip)
        return (artifact, hashlib.sha256(artifact.encode()).hexdigest(), start, end)

    def _collect_with_artifact(self, trusted, artifact, write_paths=None, **extra):
        return task_completion.collect_executor_result(
            **self._segment_collector_kwargs(
                {'segment_start': self.source_sha, 'trusted_base': trusted},
                write_paths or [self.source_path]),
            artifact=artifact,
            artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest(), **extra)

    def _assert_artifact_refused(self, trusted, artifact, code, write_paths=None, **extra):
        with self.assertRaises(LLMToolError) as raised:
            self._collect_with_artifact(trusted, artifact, write_paths, **extra)
        self.assertEqual(raised.exception.completion_code, code)

    def _assert_tip_is(self, tip, head, trusted):
        self.assertEqual(task_completion._developer_tip(
            str(self.root), self.source_sha, head, trusted), tip)

    def _assert_artifact_matches_tip(self, head, tip, trusted):
        """The artifact binds the tip; the recorded evidence still binds the full head."""
        self._assert_tip_is(tip, head, trusted)
        artifact = self._artifact(self.source_sha, tip)
        result = self._collect_with_artifact(trusted, artifact)
        self.assertEqual(result['result_head'], head)
        self.assertEqual(result['changed_paths'], sorted(self._git(
            'diff', '--name-only', '--no-renames', self.source_sha, head).splitlines()))
        self.assertEqual(result['developer_changed_paths'], [self.source_path])
        full_diff = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', '--no-ext-diff', '--no-textconv',
             '--no-renames', self.source_sha, head, '--'], cwd=self.root)
        self.assertEqual(result['diff_sha256'], hashlib.sha256(full_diff).hexdigest())

        store = TaskStore(self.base / 'clean-main-merge-task-complete')
        task = self._active_pm_task(self._task(), route='company-gw')
        task['developer'].update(
            artifact=artifact,
            artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        task['completion']['evidence'].update(
            executor_native_session_id='native-executor',
            executor_native_session='VERIFIED')
        store.create(task)
        _, tests, _, completion = self._complete_company_gw_task(
            store, task, trusted_base=trusted, tested_head=head)
        self.assertNotIn('DEVELOPER_ARTIFACT_RESULT_MISMATCH', completion['missing_gates'])
        tests.assert_called_once()

    def _main_is(self, sha):
        """Answer the authenticated main read with this commit."""
        return patch.object(task_completion, '_github_get', return_value={
            'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': sha}})

    def _adds_only_main(self, recorded, head, main_sha):
        with self._main_is(main_sha):
            return task_completion.head_adds_only_main(str(self.root), recorded, head)

    def test_head_adds_only_main_admits_a_clean_main_merge(self):
        recorded = self._developer_commit()
        trusted = self._move_main('README.md', 'Main moved.\n')
        self.assertTrue(self._adds_only_main(recorded, self._merge_main(), trusted))

    def test_head_adds_only_main_admits_two_clean_main_merges(self):
        recorded = self._developer_commit()
        self._move_main('README.md', 'Main moved.\n')
        self._merge_main()
        trusted = self._move_main('main-two.txt', 'Main moved again.\n')
        self.assertTrue(self._adds_only_main(recorded, self._merge_main(), trusted))

    def test_head_adds_only_main_refuses_a_developer_commit_after_the_merge(self):
        recorded = self._developer_commit()
        trusted = self._move_main('README.md', 'Main moved.\n')
        self._merge_main()
        head = self._developer_commit(text='# later change\n')
        self.assertFalse(self._adds_only_main(recorded, head, trusted))

    def test_head_adds_only_main_refuses_a_conflict_resolved_merge(self):
        original = (self.root / self.source_path).read_text()
        recorded = self._developer_commit()
        trusted = self._move_main(self.source_path, original + '# main change\n')
        conflicted = subprocess.run(
            ['/usr/bin/git', 'merge', '--no-ff', '-m', 'merge main', 'main'], cwd=self.root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        self.assertNotEqual(conflicted.returncode, 0)
        (self.root / self.source_path).write_text(original + '# resolved by hand\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'resolve merge')
        self.assertFalse(self._adds_only_main(recorded, self._git('rev-parse', 'HEAD'), trusted))

    def test_head_adds_only_main_refuses_a_merge_with_an_extra_change(self):
        recorded = self._developer_commit()
        trusted = self._move_main('README.md', 'Main moved.\n')
        head = self._merge_main_with_change('extra.txt')
        self.assertFalse(self._adds_only_main(recorded, head, trusted))

    def test_head_adds_only_main_refuses_a_merge_s_ours(self):
        recorded = self._developer_commit()
        trusted = self._move_main('README.md', 'Main moved.\n')
        head = self._merge_main('-s', 'ours')
        self.assertFalse(self._adds_only_main(recorded, head, trusted))

    def test_head_adds_only_main_refuses_an_octopus_merge(self):
        recorded = self._developer_commit()
        first = self._move_main('README.md', 'Main moved.\n')
        trusted = self._move_main('main-two.txt', 'Main moved again.\n')
        tree = self._git('rev-parse', self._merge_main() + '^{tree}')
        octopus = self._commit_tree(tree, recorded, first, trusted)
        self.assertFalse(self._adds_only_main(recorded, octopus, trusted))

    def test_head_adds_only_main_refuses_a_second_parent_not_on_main(self):
        recorded = self._developer_commit()
        trusted = self._move_main('README.md', 'Main moved.\n')
        self._git('checkout', '-b', 'side', self.source_sha)
        (self.root / 'side.txt').write_text('Not on main.\n')
        self._git('add', 'side.txt')
        self._git('commit', '-m', 'side change')
        self._git('checkout', 'feat/executor')
        self._git('merge', '--no-ff', '-m', 'merge side', 'side')
        head = self._git('rev-parse', 'HEAD')
        self.assertFalse(self._adds_only_main(recorded, head, trusted))

    def test_head_adds_only_main_still_passes_after_main_moves_on(self):
        recorded = self._developer_commit()
        self._move_main('README.md', 'Main moved.\n')
        head = self._merge_main()
        later = self._move_main('main-two.txt', 'Main moved after the pin.\n')
        self.assertTrue(self._adds_only_main(recorded, head, later))

    def test_head_adds_only_main_anchors_only_on_the_authenticated_read(self):
        recorded = self._developer_commit()
        self._move_main('README.md', 'Local main moved.\n')
        head = self._merge_main()
        # The local main ref holds the merged commit; the authenticated main does not.
        self.assertFalse(self._adds_only_main(recorded, head, self.source_sha))

    def test_head_adds_only_main_passes_for_a_main_merge_of_over_128_files(self):
        recorded = self._developer_commit()
        self._git('branch', 'main', self.source_sha)
        self._git('checkout', 'main')
        for index in range(130):
            (self.root / f'main-file-{index}.txt').write_text('From main.\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'main adds many files')
        trusted = self._git('rev-parse', 'HEAD')
        self._git('checkout', 'feat/executor')
        head = self._merge_main()
        self.assertTrue(self._adds_only_main(recorded, head, trusted))
        result = self._collect_with_artifact(trusted, self._artifact(self.source_sha, recorded))
        self.assertEqual(result['result_head'], head)
        self.assertGreater(len(result['changed_paths']), 128)
        self.assertEqual(result['developer_changed_paths'], [self.source_path])

    def test_head_adds_only_main_fetches_a_missing_main_commit_once(self):
        recorded = self._developer_commit()
        self._move_main('README.md', 'Main moved.\n')
        head = self._merge_main()
        upstream = self.base / 'upstream.git'
        self._git('clone', '--bare', str(self.root), str(upstream))
        newer = self._git('-C', str(upstream), '-c', 'user.name=Main',
                          '-c', 'user.email=main@example.invalid', 'commit-tree',
                          'main^{tree}', '-p', 'main', '-m', 'newer main')
        self._git('-C', str(upstream), 'update-ref', 'refs/heads/main', newer)
        self._git('remote', 'add', 'origin', str(upstream))
        with self.assertRaises(subprocess.CalledProcessError):
            self._git('cat-file', '-e', newer + '^{commit}')
        fetch = task_completion.source_freshness._fetch_origin_main
        with patch.object(task_completion.source_freshness, '_fetch_origin_main',
                          side_effect=fetch) as fetched:
            self.assertTrue(self._adds_only_main(recorded, head, newer))
        fetched.assert_called_once_with(str(self.root))

    def test_head_adds_only_main_fails_closed_when_main_stays_missing_or_unreadable(self):
        recorded = self._developer_commit()
        self._move_main('README.md', 'Main moved.\n')
        head = self._merge_main()
        with patch.object(task_completion.source_freshness,
                          '_fetch_origin_main') as fetched:
            self.assertFalse(self._adds_only_main(recorded, head, '9' * 40))
        fetched.assert_called_once_with(str(self.root))

        unreadable = (
            {'side_effect': LLMToolError('unavailable')},
            {'return_value': {'ref': 'refs/heads/master', 'object': {
                'type': 'commit', 'sha': self.source_sha}}},
            {'return_value': {'ref': 'refs/heads/main', 'object': {
                'type': 'tag', 'sha': self.source_sha}}},
        )
        for read in unreadable:
            with self.subTest(read=read), \
                    patch.object(task_completion, '_github_get', **read), \
                    patch.object(task_completion.source_freshness,
                                 '_fetch_origin_main') as fetched:
                self.assertFalse(task_completion.head_adds_only_main(
                    str(self.root), recorded, head))
                fetched.assert_not_called()

    def _repair_chain_task(self, first_head, repair_artifact):
        """Return a Task whose one review repair starts at first_head, and its store."""
        artifact = self._artifact(self.source_sha, first_head)
        task = {
            'task_binding': {'task_id': '1' * 32, 'run_id': '2' * 32,
                             'contract_digest': '3' * 64},
            'developer': {'source_sha': self.source_sha, 'write_paths': [self.source_path],
                          'artifact': artifact,
                          'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest()},
            'review_repair_claims': [{'pr': 10, 'attempt': 1, 'state': 'DISPATCHED',
                                      'head_sha': first_head,
                                      'repair_task_id': 'repair-task'}],
        }
        repair = {'developer': {
            'state': 'DEVELOPER_RESPONDED', 'source_sha': first_head,
            'artifact': repair_artifact,
            'artifact_sha256': hashlib.sha256(repair_artifact.encode()).hexdigest()}}
        return task, SimpleNamespace(get=lambda _task_id: repair)

    def _repair_head_passes(self, task, store, recorded, head, main_sha):
        evidence = {'root': str(self.root), 'common_dir': self.common,
                    'branch': 'feat/executor', 'executor_identity': 'executor-agent',
                    'executor_session_ref': 'Executor D6 session',
                    'executor_native_session_id': 'native-executor'}
        with self._main_is(main_sha):
            return result_head_admitted(task, store, evidence, executor_task=self.task_id,
                                        root=str(self.root), recorded_head=recorded, head=head)

    def test_repair_chain_head_passes_the_shared_check(self):
        recorded = self._developer_commit()
        head = self._register_head(self._developer_commit(text='# repair change\n'))
        task, store = self._repair_chain_task(recorded, self._artifact(recorded, head))
        self.assertTrue(self._repair_head_passes(task, store, recorded, head, self.source_sha))

    def test_repair_chain_head_ending_in_a_main_merge_passes_the_shared_check(self):
        recorded = self._developer_commit()
        tip = self._developer_commit(text='# repair change\n')
        trusted = self._move_main('README.md', 'Main moved.\n')
        head = self._merge_main()
        task, store = self._repair_chain_task(recorded, self._artifact(recorded, tip))
        self.assertTrue(self._repair_head_passes(task, store, recorded, head, trusted))

    def test_bare_commit_after_a_repair_chain_is_refused_by_the_shared_check(self):
        recorded = self._developer_commit()
        repaired = self._developer_commit(text='# repair change\n')
        head = self._register_head(self._developer_commit(text='# bare change\n'))
        task, store = self._repair_chain_task(recorded, self._artifact(recorded, repaired))
        self.assertFalse(self._repair_head_passes(task, store, recorded, head, self.source_sha))

    def test_new_commit_without_repair_claims_is_refused_by_the_shared_check(self):
        recorded = self._developer_commit()
        head = self._register_head(self._developer_commit(text='# repair change\n'))
        task, store = self._repair_chain_task(recorded, self._artifact(recorded, head))
        del task['review_repair_claims']
        self.assertFalse(self._repair_head_passes(task, store, recorded, head, self.source_sha))

    def test_clean_main_merge_of_other_files_is_not_developer_change(self):
        tip = self._developer_commit()
        trusted = self._move_main('README.md', 'Main moved.\n')
        head = self._merge_main()
        self.assertIn('README.md', self._git('diff', '--name-only', self.source_sha, head))
        self._assert_artifact_matches_tip(head, tip, trusted)

    def test_clean_main_merge_of_other_lines_in_the_same_file(self):
        original = (self.root / self.source_path).read_text()
        tip = self._developer_commit()
        trusted = self._move_main(self.source_path, '# main header\n' + original)
        head = self._merge_main()
        merged = self._git('show', head + ':' + self.source_path)
        self.assertIn('# main header', merged)
        self.assertIn('# developer change', merged)
        self._assert_artifact_matches_tip(head, tip, trusted)

    def test_second_trailing_clean_main_merge_is_stripped(self):
        tip = self._developer_commit()
        self._move_main('README.md', 'Main moved.\n')
        self._merge_main()
        trusted = self._move_main('main-two.txt', 'Main moved again.\n')
        head = self._merge_main()
        self._assert_artifact_matches_tip(head, tip, trusted)

    def test_repair_segments_ending_in_clean_main_merges_bind_each_tip(self):
        first_tip = self._developer_commit()
        self._move_main('README.md', 'Main moved.\n')
        first_end = self._merge_main()
        second_tip = self._developer_commit(text='# repair change\n')
        trusted = self._move_main('main-two.txt', 'Main moved again.\n')
        head = self._merge_main()
        segments = [self._artifact_segment(self.source_sha, first_tip, first_end),
                    self._artifact_segment(first_end, second_tip, head)]
        result = self._collect_with_artifact(trusted, segments[0][0],
                                             artifact_segments=segments)
        self.assertEqual(result['result_head'], head)
        self.assertEqual(result['developer_changed_paths'], [self.source_path])

    def test_repair_segment_with_a_change_inside_its_main_merge_is_refused(self):
        tip = self._developer_commit()
        trusted = self._move_main('README.md', 'Main moved.\n')
        head = self._merge_main_with_change('extra.txt')
        segment = self._artifact_segment(self.source_sha, tip, head)
        self._assert_artifact_refused(
            trusted, segment[0], 'DEVELOPER_ARTIFACT_CHAIN_INVALID',
            [self.source_path, 'extra.txt'], artifact_segments=[segment])

    def test_change_around_a_main_merge_is_refused_with_its_existing_code(self):
        cases = (('outside.txt', [self.source_path], 'RESULT_SCOPE_MISMATCH'),
                 ('extra.txt', [self.source_path, 'extra.txt'],
                  'DEVELOPER_ARTIFACT_RESULT_MISMATCH'))
        for where in ('before', 'inside', 'after'):
            for path, write_paths, code in cases:
                with self.subTest(where=where, path=path):
                    self._git('reset', '--hard', self.source_sha)
                    self._git('branch', '-f', 'main', self.source_sha)
                    tip = self._developer_commit()
                    if where == 'before':
                        self._developer_commit(path)
                    trusted = self._move_main('README.md', 'Main moved.\n')
                    if where == 'inside':
                        self._merge_main_with_change(path)
                    else:
                        self._merge_main()
                    if where == 'after':
                        self._register_head(self._developer_commit(path))
                    self._assert_artifact_refused(
                        trusted, self._artifact(self.source_sha, tip), code, write_paths)

    def test_merge_with_an_untrusted_second_parent_is_refused(self):
        tip = self._developer_commit()
        trusted = self._move_main('README.md', 'Main moved.\n')
        self._git('checkout', '-b', 'untrusted', 'main')
        self._developer_commit('side.txt')
        self._git('checkout', 'feat/executor')
        self._git('merge', '--no-ff', '-m', 'merge untrusted branch', 'untrusted')
        head = self._register_head(self._git('rev-parse', 'HEAD'))
        self._assert_tip_is(head, head, trusted)
        artifact = self._artifact(self.source_sha, tip)
        self._assert_artifact_refused(trusted, artifact, 'RESULT_SCOPE_MISMATCH')
        self._assert_artifact_refused(
            trusted, artifact, 'DEVELOPER_ARTIFACT_RESULT_MISMATCH',
            [self.source_path, 'README.md', 'side.txt'])

    def test_octopus_merge_of_trusted_branches_is_refused(self):
        tip = self._developer_commit()
        self._git('branch', 'main', self.source_sha)
        for branch in ('main-one', 'main-two'):
            self._git('checkout', '-b', branch, self.source_sha)
            self._developer_commit(branch + '.txt')
        self._git('checkout', 'main')
        self._git('merge', '--no-ff', '-m', 'main octopus', 'main-one', 'main-two')
        trusted = self._git('rev-parse', 'HEAD')
        self._git('checkout', 'feat/executor')
        self._git('merge', '--no-ff', '-m', 'developer octopus', 'main-one', 'main-two')
        head = self._register_head(self._git('rev-parse', 'HEAD'))
        self.assertEqual(len(self._git('rev-list', '--parents', '-n', '1', head).split()), 4)
        self._assert_tip_is(head, head, trusted)
        self._assert_artifact_refused(
            trusted, self._artifact(self.source_sha, tip), 'RESULT_DIFF_INVALID')

    def test_main_conflict_resolved_by_hand_is_refused(self):
        original = (self.root / self.source_path).read_text()
        tip = self._developer_commit()
        trusted = self._move_main(self.source_path, original + '# main change\n')
        merge = subprocess.run(['/usr/bin/git', 'merge', '--no-ff', '-m', 'merge main', 'main'],
                               cwd=self.root, text=True, capture_output=True)
        self.assertNotEqual(merge.returncode, 0, merge.stdout + merge.stderr)
        (self.root / self.source_path).write_text(original + '# resolved by hand\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'resolve main conflict by hand')
        head = self._register_head(self._git('rev-parse', 'HEAD'))
        self._assert_tip_is(head, head, trusted)
        self._assert_artifact_refused(trusted, self._artifact(self.source_sha, tip),
                                      'DEVELOPER_ARTIFACT_RESULT_TREE_MISMATCH')

    def test_main_merge_made_with_strategy_ours_is_refused(self):
        tip = self._developer_commit()
        trusted = self._move_main('README.md', 'Main moved.\n')
        head = self._merge_main('-s', 'ours')
        self.assertEqual(self._git('rev-parse', head + '^{tree}'),
                         self._git('rev-parse', tip + '^{tree}'))
        self._assert_tip_is(head, head, trusted)
        self._assert_artifact_refused(trusted, self._artifact(self.source_sha, tip),
                                      'RESULT_SCOPE_MISMATCH')

    def test_out_of_scope_developer_file_is_refused(self):
        result_sha = self._make_path_result('README.md', 'Developer edit.\n')
        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', self.source_sha, result_sha],
            cwd=self.root, text=True)
        with self.assertRaises(LLMToolError) as raised:
            self._collect_result(result_sha, artifact)
        self.assertEqual(raised.exception.completion_code, 'RESULT_SCOPE_MISMATCH')

    def test_result_collection_ignores_replacement_commit_and_environment_override(self):
        result_sha, artifact = self._make_result()
        replacement_tree = self._git('rev-parse', self.source_sha + '^{tree}')
        replacement_sha = self._git('commit-tree', replacement_tree, '-p', self.source_sha,
                                    '-m', 'replacement with source tree')
        self._git('replace', result_sha, replacement_sha)

        with patch.dict(os.environ, {'GIT_NO_REPLACE_OBJECTS': '0'}):
            result = self._collect_result(result_sha, artifact)

        self.assertEqual(result['result_head'], result_sha)
        self.assertEqual(result['changed_paths'], [self.source_path])

    def test_result_collection_refuses_hidden_out_of_scope_write_with_replace_config(self):
        result_sha = self._make_path_result('README.md', 'Executor changed docs.\n')
        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', self.source_sha, result_sha],
            cwd=self.root, text=True)
        replacement_tree = self._git('rev-parse', self.source_sha + '^{tree}')
        replacement_sha = self._git('commit-tree', replacement_tree, '-p', self.source_sha,
                                    '-m', 'replacement hiding README write')
        self._git('config', 'core.useReplaceRefs', 'true')
        self._git('replace', result_sha, replacement_sha)

        with patch.dict(os.environ, {'GIT_NO_REPLACE_OBJECTS': '0'}):
            with self.assertRaises(LLMToolError) as rejected:
                self._collect_result(result_sha, artifact)
        self.assertEqual(rejected.exception.completion_code, 'RESULT_SCOPE_MISMATCH')

    def test_result_collection_ignores_replacement_commit_for_ancestry(self):
        result_sha, artifact = self._make_result()
        replacement_tree = self._git('rev-parse', self.source_sha + '^{tree}')
        replacement_sha = self._git('commit-tree', replacement_tree, '-m', 'unrelated replacement')
        self._git('replace', self.source_sha, replacement_sha)

        result = self._collect_result(result_sha, artifact)
        self.assertEqual(result['source_sha'], self.source_sha)
        self.assertEqual(result['result_head'], result_sha)

    def test_result_collection_refuses_legacy_grafts(self):
        result_sha, artifact = self._make_result()
        grafts = Path(self.common) / 'info' / 'grafts'
        grafts.parent.mkdir(parents=True, exist_ok=True)
        grafts.write_text('')

        with self.assertRaises(LLMToolError) as rejected:
            self._collect_result(result_sha, artifact)
        self.assertEqual(rejected.exception.completion_code, 'LEGACY_GRAFTS_UNSUPPORTED')

    def test_result_collection_refuses_symlink_legacy_grafts(self):
        result_sha, artifact = self._make_result()
        grafts = Path(self.common) / 'info' / 'grafts'
        grafts.parent.mkdir(parents=True, exist_ok=True)
        target = self.base / 'grafts-target'
        target.write_text('')
        grafts.symlink_to(target)

        with self.assertRaises(LLMToolError) as rejected:
            self._collect_result(result_sha, artifact)
        self.assertEqual(rejected.exception.completion_code, 'LEGACY_GRAFTS_UNSUPPORTED')

    def test_worktree_legacy_grafts_are_refused(self):
        worktree = self.base / 'linked-worktree'
        self._git('worktree', 'add', '--detach', str(worktree), self.source_sha)
        git_dir = Path(subprocess.check_output(
            ['/usr/bin/git', 'rev-parse', '--absolute-git-dir'], cwd=worktree,
            text=True).strip())
        grafts = git_dir / 'info' / 'grafts'
        grafts.parent.mkdir(parents=True, exist_ok=True)
        grafts.mkdir()

        with self.assertRaises(LLMToolError) as rejected:
            task_completion._reject_legacy_grafts(str(worktree), self.common)
        self.assertEqual(rejected.exception.completion_code, 'LEGACY_GRAFTS_UNSUPPORTED')

    def test_advanced_registered_base_completes_from_dispatch_source(self):
        path = self.root / self.source_path
        path.write_text(path.read_text() + '\n# approved branch progress\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'approved branch progress')
        dispatch_source = self._git('rev-parse', 'HEAD')
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record['head_sha'] = dispatch_source
        self._write_env2_marker(record, self.root, submitted_head=dispatch_source,
                                current_head=dispatch_source)
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)

        registration, snapshot = task_completion.bind_executor_task(
            self.task_id, expected_common_dir=self.common, root=str(self.root))
        self.assertEqual(registration.source_sha, self.source_sha)
        self.assertEqual(snapshot.head, dispatch_source)
        result_sha, artifact = self._make_result(source_sha=dispatch_source)
        result = task_completion.collect_executor_result(
            task_id=self.task_id, expected_common_dir=self.common,
            expected_root=str(self.root), expected_branch='feat/executor',
            source_sha=dispatch_source,
            task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                          'contract_digest': '3' * 64},
            expected_agent='executor-agent', expected_session_ref='Executor D6 session',
            expected_native_session_id='native-executor', write_paths=[self.source_path],
            artifact=artifact,
            artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        self.assertEqual(result['source_sha'], dispatch_source)
        self.assertEqual(result['result_head'], result_sha)
        self.assertEqual(result['changed_paths'], [self.source_path])

    def test_record_result_unblocks_task_completion_head_binding(self):
        task_root = self.base / 'executor-result-worktree'
        self._git('worktree', 'add', '-b', 'feat/executor-result', str(task_root),
                  self.source_sha)
        task_root = task_root.resolve()
        self._write_registration(self.task_id, 'executor-agent', 'Executor D6 session',
                                 task_root, 'feat/executor-result', self.source_sha,
                                 self.source_sha, native_session_id='native-executor')
        path = task_root / self.source_path
        path.write_text(path.read_text() + '\n# committed developer result\n')
        subprocess.run(['/usr/bin/git', 'add', self.source_path], cwd=task_root, check=True)
        subprocess.run(['/usr/bin/git', '-c', 'user.name=Executor', '-c',
                        'user.email=executor@example.invalid', 'commit', '-m',
                        'committed developer result'], cwd=task_root, check=True,
                       capture_output=True)
        result_sha = subprocess.check_output(['/usr/bin/git', 'rev-parse', 'HEAD'],
                                             cwd=task_root, text=True).strip()
        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', self.source_sha, result_sha],
            cwd=task_root, text=True)
        kwargs = dict(task_id=self.task_id, expected_common_dir=self.common,
            expected_root=str(task_root), expected_branch='feat/executor-result',
            source_sha=self.source_sha,
            task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                          'contract_digest': '3' * 64},
            expected_agent='executor-agent', expected_session_ref='Executor D6 session',
            expected_native_session_id='native-executor', write_paths=[self.source_path],
            artifact=artifact,
            artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        with self.assertRaises(LLMToolError) as raised:
            task_completion.collect_executor_result(**kwargs)
        self.assertEqual(raised.exception.completion_code, 'RESULT_HEAD_MISMATCH')

        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')

        def complete_bootstrap(bootstrap_root, bootstrap_task_id):
            self.assertEqual(bootstrap_root, task_root)
            self.assertEqual(bootstrap_task_id, self.task_id)
            current = d6_worktree_lifecycle._read_registry_record(registry_path)
            self._write_env2_marker(current, task_root, submitted_head=result_sha,
                                    current_head=result_sha)
            d6_worktree_lifecycle._replace_record(registry_path, current)

        # #369: the lifecycle reads trust.json; use a private temp trust home so
        # the real ~/.config/llmtool/trust.json is never read.
        trust_home = self.base / 'trust-home'
        trust_dir = trust_home / '.config' / 'llmtool'
        trust_dir.mkdir(parents=True, mode=0o700)
        for directory in (trust_home, trust_home / '.config', trust_dir):
            directory.chmod(0o700)
        trust_path = trust_dir / 'trust.json'
        trust_path.write_text(json.dumps({
            'schema_version': 1, 'canonical_root': str(self.root),
            'workspace_root': str(self.base), 'tasks_root': str(self.base),
            'repository': {'slug': 'davidcatcat/davidcattoolsproject',
                           'allowed_origins': [
                               'git@github.com:davidcatcat/davidcattoolsproject.git'],
                           'approval_refs': {}},
            'ci_trust': {}, 'tools': {},
        }), encoding='utf-8')
        trust_path.chmod(0o600)
        original_load_trust = d6_worktree_lifecycle._load_trust

        with patch.dict(os.environ, {'LLMTOOL_D6_TESTING': '1'}), \
                patch.object(d6_worktree_lifecycle, '_load_trust',
                             side_effect=lambda: original_load_trust(home=str(trust_home))), \
                patch.object(d6_worktree_lifecycle, '_bootstrap',
                             side_effect=complete_bootstrap) as bootstrap:
            recorded = d6_worktree_lifecycle.record_result(
                task_id=self.task_id, canonical_root=self.root, root=task_root,
                workspace_root=self.base)
        bootstrap.assert_called_once_with(task_root, self.task_id)
        self.assertEqual(recorded, result_sha)
        result = task_completion.collect_executor_result(**kwargs)
        self.assertEqual(result['result_head'], result_sha)

    def test_actual_registered_diff_and_bounded_test_subprocess_bind_exact_head(self):
        result_sha, artifact = self._make_result()
        objects_before = self._git('count-objects', '-v')
        binding = {'task_id': '1' * 32, 'run_id': '2' * 32,
                   'contract_digest': '3' * 64}
        result = task_completion.collect_executor_result(
            task_id=self.task_id, expected_common_dir=self.common,
            expected_root=str(self.root), expected_branch='feat/executor',
            source_sha=self.source_sha, task_binding=binding,
            expected_agent='executor-agent', expected_session_ref='Executor D6 session',
            expected_native_session_id='native-executor', write_paths=[self.source_path],
            artifact=artifact, artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        self.assertEqual(self._git('count-objects', '-v'), objects_before)
        self.assertEqual(result['result_head'], result_sha)
        self.assertEqual(result['changed_paths'], [self.source_path])
        tests = task_completion.run_bounded_tests(
            root=str(self.root), source_sha=self.source_sha, result_sha=result_sha, read_paths=[self.source_path],
            write_paths=[self.source_path])
        self.assertEqual(tests[0]['exit_code'], 0)
        self.assertEqual(tests[0]['source_sha'], result_sha)
        self.assertRegex(tests[0]['output_sha256'], r'^[0-9a-f]{64}$')

    def test_result_collection_rejects_missing_env2_pass_marker(self):
        result_sha, artifact = self._make_result()
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record.pop('env2_pass_marker')
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)
        with self.assertRaises(LLMToolError) as raised:
            task_completion.collect_executor_result(
                task_id=self.task_id, expected_common_dir=self.common,
                expected_root=str(self.root), expected_branch='feat/executor',
                source_sha=self.source_sha,
                task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                              'contract_digest': '3' * 64},
                expected_agent='executor-agent', expected_session_ref='Executor D6 session',
                expected_native_session_id='native-executor', write_paths=[self.source_path],
                artifact=artifact, artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        self.assertEqual(raised.exception.completion_code, 'D6_ENV2_PASS_MARKER_MISSING')

    def test_result_collection_rejects_marker_for_unapproved_tooling_sha(self):
        result_sha, artifact = self._make_result()
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record['env2_approved_tooling_sha'] = result_sha
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)
        with self.assertRaises(LLMToolError) as raised:
            task_completion.collect_executor_result(
                task_id=self.task_id, expected_common_dir=self.common,
                expected_root=str(self.root), expected_branch='feat/executor',
                source_sha=self.source_sha,
                task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                              'contract_digest': '3' * 64},
                expected_agent='executor-agent', expected_session_ref='Executor D6 session',
                expected_native_session_id='native-executor', write_paths=[self.source_path],
                artifact=artifact, artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        self.assertEqual(raised.exception.completion_code, 'D6_ENV2_PASS_MARKER_MISMATCH')

    def test_result_collection_rejects_env2_marker_stale_after_commit(self):
        result_sha, artifact = self._make_result(refresh_env2_marker=False)
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)
        with self.assertRaises(LLMToolError) as raised:
            task_completion.collect_executor_result(
                task_id=self.task_id, expected_common_dir=self.common,
                expected_root=str(self.root), expected_branch='feat/executor',
                source_sha=self.source_sha,
                task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                              'contract_digest': '3' * 64},
                expected_agent='executor-agent', expected_session_ref='Executor D6 session',
                expected_native_session_id='native-executor', write_paths=[self.source_path],
                artifact=artifact, artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        self.assertEqual(raised.exception.completion_code, 'D6_ENV2_PASS_MARKER_MISMATCH')

    def test_uncommitted_worktree_result_binds_live_diff_without_result_head(self):
        path = self.root / self.source_path
        path.write_text('import unittest\nclass Fixture(unittest.TestCase):\n    def test_ok(self): self.assertEqual(2, 2)\n')
        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', '--no-ext-diff', '--no-textconv',
             '--no-renames', self.source_sha, '--'], cwd=self.root)
        binding = {'task_id': '1' * 32, 'run_id': '2' * 32,
                   'contract_digest': '3' * 64}
        result = task_completion.collect_executor_worktree_result(
            task_id=self.task_id, expected_common_dir=self.common,
            expected_root=str(self.root), expected_branch='feat/executor',
            source_sha=self.source_sha, task_binding=binding,
            expected_agent='executor-agent', expected_session_ref='Executor D6 session',
            expected_native_session_id='native-executor', read_paths=[self.source_path],
            write_paths=[self.source_path], artifact=artifact.decode('utf-8'),
            artifact_sha256=hashlib.sha256(artifact).hexdigest())
        self.assertEqual(result['task_id'], binding['task_id'])
        self.assertEqual(result['run_id'], binding['run_id'])
        self.assertIsNone(result['result_head'])
        self.assertEqual(result['result_mode'], 'UNCOMMITTED_WORKTREE')
        self.assertEqual(result['source_sha'], self.source_sha)
        self.assertEqual(result['changed_paths'], [self.source_path])
        self.assertEqual(result['diff_sha256'], hashlib.sha256(artifact).hexdigest())

    def test_uncommitted_worktree_result_refuses_legacy_grafts(self):
        path = self.root / self.source_path
        path.write_text(path.read_text() + '# uncommitted task result\n')
        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', '--no-ext-diff', '--no-textconv',
             '--no-renames', self.source_sha, '--'], cwd=self.root)
        grafts = Path(self.common) / 'info' / 'grafts'
        grafts.parent.mkdir(parents=True, exist_ok=True)
        grafts.write_text('')

        with self.assertRaises(LLMToolError) as rejected:
            task_completion.collect_executor_worktree_result(
                task_id=self.task_id, expected_common_dir=self.common,
                expected_root=str(self.root), expected_branch='feat/executor',
                source_sha=self.source_sha,
                task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                              'contract_digest': '3' * 64},
                expected_agent='executor-agent', expected_session_ref='Executor D6 session',
                expected_native_session_id='native-executor', write_paths=[self.source_path],
                read_paths=[self.source_path], artifact=artifact.decode('utf-8'),
                artifact_sha256=hashlib.sha256(artifact).hexdigest())
        self.assertEqual(rejected.exception.completion_code, 'LEGACY_GRAFTS_UNSUPPORTED')

    def test_worktree_docs_only_gate_returns_explicit_non_test_record(self):
        docs = self.root / 'README.md'
        docs.write_text('Documentation.\n')
        patch_bytes = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', '--no-ext-diff', '--no-textconv',
             '--no-renames', self.source_sha, '--'], cwd=self.root)
        digest = hashlib.sha256(patch_bytes).hexdigest()
        evidence = task_completion.run_bounded_worktree_tests(root=str(self.root),
            source_sha=self.source_sha, diff_sha256=digest, changed_paths=['README.md'],
            read_paths=[], write_paths=['README.md'])
        self.assertEqual(evidence[0]['status'], 'NOT_APPLICABLE_DOCS_ONLY')
        self.assertTrue(evidence[0]['docs_only'])
        self.assertNotIn('exit_code', evidence[0])

    def test_worktree_snapshot_includes_only_regular_in_scope_new_files(self):
        new_path = 'tools/llmtool/tests/new_fixture.py'
        target = self.root / new_path
        target.write_text('VALUE = 7\n')
        before_index = task_completion._git(self.root, ('write-tree',))
        snapshot = task_completion._worktree_snapshot(
            root=self.root, source_sha=self.source_sha,
            read_paths=[new_path], write_paths=[new_path])
        self.assertEqual(snapshot['changed_paths'], [new_path])
        self.assertIn(b'new file mode 100644', snapshot['diff'])
        self.assertIn(b'+++ b/' + new_path.encode(), snapshot['diff'])
        self.assertEqual(snapshot['diff_sha256'], hashlib.sha256(snapshot['diff']).hexdigest())
        self.assertEqual(task_completion._git(self.root, ('write-tree',)), before_index)

        outside = self.root / 'outside.py'
        outside.write_text('VALUE = 8\n')
        with self.assertRaises(LLMToolError) as rejected:
            task_completion._worktree_snapshot(
                root=self.root, source_sha=self.source_sha,
                read_paths=[new_path], write_paths=[new_path])
        self.assertEqual(rejected.exception.completion_code, 'WORKTREE_UNTRACKED_FILES')
        outside.unlink()

        target.unlink()
        target.symlink_to(self.root / self.source_path)
        with self.assertRaises(LLMToolError) as symlink:
            task_completion._worktree_snapshot(
                root=self.root, source_sha=self.source_sha,
                read_paths=[new_path], write_paths=[new_path])
        self.assertEqual(symlink.exception.completion_code, 'WORKTREE_FILE_UNSUPPORTED')

    def test_worktree_snapshot_rejects_tracked_change_outside_write_paths(self):
        in_path = self.root / 'in.py'
        out_path = self.root / 'out.py'
        in_path.write_text('VALUE = 1\n')
        out_path.write_text('VALUE = 2\n')
        self._git('add', 'in.py', 'out.py')
        self._git('commit', '-m', 'add scoped files')
        self.source_sha = self._git('rev-parse', 'HEAD')
        out_path.write_text('VALUE = 3\n')

        with self.assertRaises(LLMToolError) as rejected:
            task_completion._worktree_snapshot(
                root=self.root, source_sha=self.source_sha,
                read_paths=['in.py'], write_paths=['in.py'])
        self.assertEqual(rejected.exception.completion_code, 'WORKTREE_SCOPE_MISMATCH')

    def test_uncommitted_worktree_rejects_stale_artifact_or_untracked_file(self):
        path = self.root / self.source_path
        path.write_text(path.read_text() + '\n# live uncommitted change\n')
        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', '--no-ext-diff', '--no-textconv',
             '--no-renames', self.source_sha, '--'], cwd=self.root).decode('utf-8')
        binding = {'task_id': '1' * 32, 'run_id': '2' * 32,
                   'contract_digest': '3' * 64}
        with self.assertRaises(LLMToolError) as stale:
            task_completion.collect_executor_worktree_result(
                task_id=self.task_id, expected_common_dir=self.common,
                expected_root=str(self.root), expected_branch='feat/executor',
                source_sha=self.source_sha, task_binding=binding,
                expected_agent='executor-agent', expected_session_ref='Executor D6 session',
                expected_native_session_id='native-executor', read_paths=[self.source_path],
                write_paths=[self.source_path], artifact=artifact + '# caller edit\n',
                artifact_sha256=hashlib.sha256((artifact + '# caller edit\n').encode()).hexdigest())
        self.assertEqual(stale.exception.completion_code, 'WORKTREE_ARTIFACT_MISMATCH')

        stray = self.root / 'unexpected.txt'
        stray.write_text('untracked')
        with self.assertRaises(LLMToolError) as untracked:
            task_completion.collect_executor_worktree_result(
                task_id=self.task_id, expected_common_dir=self.common,
                expected_root=str(self.root), expected_branch='feat/executor',
                source_sha=self.source_sha, task_binding=binding,
                expected_agent='executor-agent', expected_session_ref='Executor D6 session',
                expected_native_session_id='native-executor', read_paths=[self.source_path],
                write_paths=[self.source_path], artifact=artifact,
                artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        self.assertEqual(untracked.exception.completion_code, 'WORKTREE_UNTRACKED_FILES')

    def test_caller_injected_sha_wrong_task_contract_and_artifact_are_rejected(self):
        registration, snapshot = task_completion.bind_executor_task(
            self.task_id, expected_common_dir=self.common, root=str(self.root))
        self.assertEqual(registration.source_sha, snapshot.head)
        with self.assertRaises(LLMToolError):
            task_completion.read_d6_registration('../other', self.common)
        with self.assertRaises(LLMToolError):
            task_completion.validate_task_binding(
                stored_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                                'contract_digest': '3' * 64}, task_id='1' * 32,
                run_id='2' * 32, contract_digest='4' * 64)
        for key, wrong in (('task_id', '9' * 32), ('run_id', '8' * 32)):
            binding = {'task_id': '1' * 32, 'run_id': '2' * 32,
                       'contract_digest': '3' * 64}
            binding[key] = wrong
            with self.subTest(key=key), self.assertRaises(LLMToolError):
                task_completion.validate_task_binding(
                    stored_binding=binding, task_id='1' * 32, run_id='2' * 32,
                    contract_digest='3' * 64)
        result_sha, _ = self._make_result()
        with self.assertRaises(LLMToolError):
            task_completion.collect_executor_result(
                task_id=self.task_id, expected_common_dir=self.common,
                expected_root=str(self.root), expected_branch='feat/executor',
                source_sha=self.source_sha,
                task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                              'contract_digest': '3' * 64},
                expected_agent='executor-agent', expected_session_ref='Executor D6 session',
                expected_native_session_id='native-executor', write_paths=[self.source_path],
                artifact='fake patch', artifact_sha256=hashlib.sha256(b'fake patch').hexdigest())

    def test_d6_registration_accepts_exact_lifecycle_physical_identity(self):
        path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(path.read_text())
        self.assertEqual(set(record['physical_identity']),
                         {'device', 'inode', 'uid', 'mode'})
        self.assertEqual(record['physical_identity']['mode'], '0o700')
        registration, snapshot = task_completion.bind_executor_task(
            self.task_id, expected_common_dir=self.common, root=str(self.root))
        self.assertEqual(registration.root, str(self.root))
        self.assertEqual(snapshot.head, self.source_sha)

    def test_d6_registration_rejects_noncanonical_physical_modes(self):
        path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        original = json.loads(path.read_text())
        for mode in ('0700', '0o770', 448, None):
            with self.subTest(mode=mode):
                record = json.loads(json.dumps(original))
                record['physical_identity']['mode'] = mode
                path.write_text(json.dumps(record))
                path.chmod(0o600)
                with self.assertRaises(LLMToolError) as rejected:
                    task_completion.read_d6_registration(self.task_id, self.common)
                self.assertEqual(rejected.exception.completion_code,
                                 'D6_REGISTRY_INVALID')
        path.write_text(json.dumps(original))
        path.chmod(0o600)

    def test_legacy_d6_physical_identity_requires_owner_only_root_mode(self):
        path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(path.read_text())
        del record['physical_identity']['mode']
        path.write_text(json.dumps(record))
        path.chmod(0o600)
        registration, snapshot = task_completion.bind_executor_task(
            self.task_id, expected_common_dir=self.common, root=str(self.root))
        self.assertEqual(registration.root, str(self.root))
        self.assertEqual(snapshot.head, self.source_sha)

        self.root.chmod(0o755)
        try:
            with self.assertRaises(LLMToolError) as rejected:
                task_completion.read_d6_registration(self.task_id, self.common)
            self.assertEqual(rejected.exception.completion_code,
                             'D6_ROOT_IDENTITY_MISMATCH')
        finally:
            self.root.chmod(0o700)

    def test_matching_non_string_binding_values_are_rejected(self):
        for key in ('task_id', 'run_id', 'contract_digest'):
            for bad in (None, 123, False):
                binding = {'task_id': '1' * 32, 'run_id': '2' * 32,
                           'contract_digest': '3' * 64}
                binding[key] = bad
                supplied = dict(binding)
                with self.subTest(key=key, bad=bad), \
                        self.assertRaises(LLMToolError) as rejected:
                    task_completion.validate_task_binding(
                        stored_binding=binding, task_id=supplied['task_id'],
                        run_id=supplied['run_id'],
                        contract_digest=supplied['contract_digest'])
                self.assertEqual(rejected.exception.completion_code,
                                 'TASK_RUN_CONTRACT_MISMATCH')

    def test_dirty_tracked_source_file_is_rejected(self):
        path = self.root / self.source_path
        path.write_text(path.read_text() + '\n# uncommitted change\n')
        with self.assertRaises(LLMToolError) as rejected:
            task_completion.bind_executor_task(
                self.task_id, expected_common_dir=self.common, root=str(self.root))
        self.assertEqual(rejected.exception.completion_code, 'D6_SOURCE_BINDING_MISMATCH')

    def test_registered_branch_head_may_advance_from_its_approved_base(self):
        result_sha, _ = self._make_result()
        registration, snapshot = task_completion.bind_executor_task(
            self.task_id, expected_common_dir=self.common, root=str(self.root))
        self.assertEqual(registration.source_sha, self.source_sha)
        self.assertNotEqual(registration.source_sha, snapshot.head)
        self.assertEqual(snapshot.head, result_sha)
        self.assertEqual(registration.result_sha, snapshot.head)

    def test_registered_branch_head_must_descend_from_its_approved_base(self):
        self._make_result()
        foreign = self._git('commit-tree', self._git('rev-parse', 'HEAD^{tree}'),
                            '-m', 'unrelated base')
        path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(path.read_text())
        record['base_sha'] = foreign
        path.write_text(json.dumps(record))
        path.chmod(0o600)
        with self.assertRaises(LLMToolError) as rejected:
            task_completion.bind_executor_task(
                self.task_id, expected_common_dir=self.common, root=str(self.root))
        self.assertEqual(rejected.exception.completion_code, 'D6_SOURCE_BINDING_MISMATCH')

    def test_review_pending_executor_registration_remains_valid_for_completion_evidence(self):
        result_sha, artifact = self._make_result()
        path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        registration = json.loads(path.read_text())
        registration['state'] = 'REVIEW_PENDING'
        path.write_text(json.dumps(registration))
        path.chmod(0o600)
        result = task_completion.collect_executor_result(
            task_id=self.task_id, expected_common_dir=self.common,
            expected_root=str(self.root), expected_branch='feat/executor',
            source_sha=self.source_sha,
            task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                          'contract_digest': '3' * 64},
            expected_agent='executor-agent', expected_session_ref='Executor D6 session',
            expected_native_session_id='native-executor', write_paths=[self.source_path],
            artifact=artifact,
            artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        self.assertEqual(result['result_head'], result_sha)
        self.assertEqual(result['executor_task_id'], self.task_id)
        with self.assertRaises(LLMToolError):
            task_completion.bind_executor_task(
                self.task_id, expected_common_dir=self.common, root=str(self.root))

    def test_advanced_registered_base_accepts_uncommitted_dispatch_diff(self):
        path = self.root / self.source_path
        path.write_text(path.read_text() + '\n# approved branch progress\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'approved branch progress')
        dispatch_source = self._git('rev-parse', 'HEAD')
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record['head_sha'] = dispatch_source
        self._write_env2_marker(record, self.root, submitted_head=dispatch_source,
                                current_head=dispatch_source)
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)

        registration, snapshot = task_completion.bind_executor_task(
            self.task_id, expected_common_dir=self.common, root=str(self.root))
        self.assertEqual(registration.source_sha, self.source_sha)
        self.assertEqual(snapshot.head, dispatch_source)
        path.write_text(path.read_text() + '\n# uncommitted task result\n')
        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', dispatch_source],
            cwd=self.root, text=True)
        result = task_completion.collect_executor_worktree_result(
            task_id=self.task_id, expected_common_dir=self.common,
            expected_root=str(self.root), expected_branch='feat/executor',
            source_sha=dispatch_source, expected_result_head=dispatch_source,
            task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                          'contract_digest': '3' * 64},
            expected_agent='executor-agent', expected_session_ref='Executor D6 session',
            expected_native_session_id='native-executor', write_paths=[self.source_path],
            read_paths=[self.source_path], artifact=artifact,
            artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        self.assertEqual(result['source_sha'], dispatch_source)
        self.assertIsNone(result['result_head'])
        self.assertEqual(result['result_mode'], 'UNCOMMITTED_WORKTREE')
        self.assertEqual(result['changed_paths'], [self.source_path])

    def test_artifact_must_forward_apply_to_the_exact_result_tree(self):
        result_sha, _ = self._make_result()
        wrong_patch = ('diff --git a/' + self.source_path + ' b/' + self.source_path +
            '\nindex 0000000..1111111 100644\n--- a/' + self.source_path +
            '\n+++ b/' + self.source_path +
            '\n@@ -3 +3 @@ class Fixture(unittest.TestCase):\n'
            '-    def test_ok(self): self.assertEqual(0, 0)\n'
            '+    def test_ok(self): self.assertEqual(1, 1)\n')
        with self.assertRaises(LLMToolError) as rejected:
            task_completion.verify_artifact_applied(
                str(self.root), wrong_patch,
                hashlib.sha256(wrong_patch.encode()).hexdigest(),
                self.source_sha, result_sha, [self.source_path], [self.source_path])
        self.assertEqual(rejected.exception.completion_code, 'DEVELOPER_ARTIFACT_NOT_APPLIED')

    def test_artifact_materialization_has_aggregate_byte_and_file_count_caps(self):
        result_sha, artifact = self._make_result()
        digest = hashlib.sha256(artifact.encode()).hexdigest()
        with patch('llmtool.task_completion._proposal_blob_size',
                   side_effect=[task_completion._PROPOSAL_TREE_LIMIT - 1, 1]):
            task_completion.verify_artifact_applied(
                str(self.root), artifact, digest, self.source_sha, result_sha,
                [self.source_path], [self.source_path])
        with patch('llmtool.task_completion._proposal_blob_size',
                   side_effect=[task_completion._PROPOSAL_TREE_LIMIT, 1]), \
             patch('llmtool.task_completion.tempfile.TemporaryDirectory') as temporary_dir:
            with self.assertRaises(LLMToolError) as rejected:
                task_completion.verify_artifact_applied(
                    str(self.root), artifact, digest, self.source_sha, result_sha,
                    [self.source_path], [self.source_path])
        self.assertEqual(rejected.exception.completion_code, 'DEVELOPER_ARTIFACT_TREE_TOO_LARGE')
        temporary_dir.assert_not_called()

        paths = [f'file-{index}.txt' for index in range(task_completion._PROPOSAL_FILE_LIMIT + 1)]
        many_files = ''.join(f'diff --git a/{path} b/{path}\n' for path in paths)
        with patch('llmtool.task_completion.tempfile.TemporaryDirectory') as temporary_dir:
            with self.assertRaises(LLMToolError) as rejected:
                task_completion.verify_artifact_applied(
                    str(self.root), many_files,
                    hashlib.sha256(many_files.encode()).hexdigest(),
                    self.source_sha, result_sha, paths, paths)
        self.assertEqual(rejected.exception.completion_code,
                         'DEVELOPER_ARTIFACT_RESULT_MISMATCH')
        temporary_dir.assert_not_called()

    def test_failed_test_subprocess_is_recorded_with_nonzero_status(self):
        result_sha, _ = self._make_result(code='    def test_fail(self): self.fail("expected")\n')
        evidence = task_completion.run_bounded_tests(
            root=str(self.root), source_sha=self.source_sha, result_sha=result_sha, read_paths=[self.source_path],
            write_paths=[self.source_path])
        self.assertNotEqual(evidence[0]['exit_code'], 0)
        self.assertEqual(evidence[0]['status'], 'FAILED')
        self.assertIsInstance(evidence[0]['output_excerpt'], list)

    def test_registered_scripts_run_isolated_and_failure_is_evidenced(self):
        path = 'scripts/test_fixture.py'
        script = ('import unittest\n'
                  'class Fixture(unittest.TestCase):\n'
                  '    def test_fail(self): self.fail("expected failure")\n'
                  'if __name__ == "__main__": unittest.main()\n')
        result_sha = self._make_path_result(path, script)
        evidence = task_completion.run_bounded_tests(
            root=str(self.root), source_sha=self.source_sha, result_sha=result_sha, read_paths=[path], write_paths=[])
        self.assertEqual(evidence[0]['path'], path)
        self.assertEqual(evidence[0]['status'], 'FAILED')
        self.assertNotEqual(evidence[0]['exit_code'], 0)
        self.assertTrue(evidence[0]['output_excerpt'])
        self.assertTrue(all('expected failure' in item['text']
                            for item in evidence[0]['output_excerpt']))

    def test_registered_script_discovers_all_classes_past_early_main_exit(self):
        path = 'scripts/test_fixture.py'
        expected_argv = [sys.executable, '-I', '-B', '-m', 'unittest',
                         'discover', '-s', 'scripts', '-p', 'test_fixture.py']
        expected_command_sha = hashlib.sha256(
            json.dumps(expected_argv, separators=(',', ':')).encode()).hexdigest()

        failing_later_script = (
            'import unittest\n'
            'class EarlyFixture(unittest.TestCase):\n'
            '    def test_early(self): self.assertTrue(True)\n'
            'if __name__ == "__main__":\n'
            '    raise SystemExit(0)\n'
            'class LaterFixture(unittest.TestCase):\n'
            '    def test_later_fail(self): self.fail("later class failure")\n'
        )
        result_sha = self._make_path_result(path, failing_later_script)
        evidence = task_completion.run_bounded_tests(
            root=str(self.root), source_sha=self.source_sha,
            result_sha=result_sha, read_paths=[path], write_paths=[])
        self.assertEqual(evidence[0]['path'], path)
        self.assertEqual(evidence[0]['command_sha256'], expected_command_sha)
        self.assertEqual(evidence[0]['status'], 'FAILED')
        self.assertNotEqual(evidence[0]['exit_code'], 0)
        self.assertTrue(evidence[0]['output_excerpt'])
        self.assertTrue(any('later class failure' in item['text']
                            for item in evidence[0]['output_excerpt']))

        passing_all_classes_script = (
            'import unittest\n'
            'RAN = []\n'
            'class FirstFixture(unittest.TestCase):\n'
            '    def test_first(self):\n'
            '        RAN.append("first")\n'
            '        self.assertIn("SecondFixture", globals())\n'
            'if __name__ == "__main__":\n'
            '    raise SystemExit(0)\n'
            'class SecondFixture(unittest.TestCase):\n'
            '    def test_second(self):\n'
            '        RAN.append("second")\n'
            '        self.assertEqual(RAN, ["first", "second"])\n'
        )
        result_sha = self._make_path_result(path, passing_all_classes_script)
        evidence = task_completion.run_bounded_tests(
            root=str(self.root), source_sha=self.source_sha,
            result_sha=result_sha, read_paths=[path], write_paths=[])
        self.assertEqual(evidence[0]['path'], path)
        self.assertEqual(evidence[0]['command_sha256'], expected_command_sha)
        self.assertEqual(evidence[0]['exit_code'], 0)
        self.assertEqual(evidence[0]['status'], 'PASSED')

    def test_registered_script_with_no_tests_is_not_passed(self):
        path = 'scripts/test_fixture.py'
        for label, script in (
            ('empty_module', 'VALUE = 1\n'),
            ('main_only_exit_zero', 'if __name__ == "__main__":\n    raise SystemExit(0)\n'),
            ('empty_testcase', 'import unittest\nclass Empty(unittest.TestCase):\n    pass\n'),
            ('import_time_exit_zero', 'raise SystemExit(0)\n'),
        ):
            with self.subTest(case=label):
                result_sha = self._make_path_result(path, script)
                evidence = task_completion.run_bounded_tests(
                    root=str(self.root), source_sha=self.source_sha,
                    result_sha=result_sha, read_paths=[path], write_paths=[])
                self.assertEqual(evidence[0]['path'], path)
                self.assertNotEqual(evidence[0]['status'], 'PASSED')
                self.assertEqual(evidence[0]['status'], 'FAILED')

    def test_out_of_registry_task_path_does_not_add_a_test_entry(self):
        result_sha = self._make_path_result('docs/guide.md', 'Documentation.\n')
        evidence = task_completion.run_bounded_tests(
            root=str(self.root), source_sha=self.source_sha, result_sha=result_sha,
            read_paths=['scripts/anything_else.py'], write_paths=[])
        self.assertEqual(evidence[0]['status'], 'NOT_APPLICABLE_DOCS_ONLY')
        self.assertEqual(evidence[0]['changed_paths'], ['docs/guide.md'])

    def test_docs_only_gate_uses_actual_git_paths(self):
        result_sha = self._make_path_result('docs/guide.md', 'Documentation.\n')
        evidence = task_completion.run_bounded_tests(
            root=str(self.root), source_sha=self.source_sha, result_sha=result_sha,
            read_paths=['tools/llmtool/tests/test_fixture.py'], write_paths=[])
        self.assertEqual(evidence[0]['status'], 'NOT_APPLICABLE_DOCS_ONLY')

    def test_multi_commit_result_is_not_misclassified_as_docs_only(self):
        code_path = 'tools/llmtool/llmtool/x.py'
        (self.root / 'tools' / 'llmtool' / 'llmtool').mkdir(parents=True)
        (self.root / code_path).write_text('VALUE = 1\n')
        self._git('add', code_path)
        self._git('commit', '-m', 'code change')
        (self.root / 'README.md').write_text('Documentation.\n')
        self._git('add', 'README.md')
        self._git('commit', '-m', 'docs change')
        result_sha = self._git('rev-parse', 'HEAD')
        with self.assertRaises(LLMToolError) as raised:
            task_completion.run_bounded_tests(root=str(self.root), source_sha=self.source_sha,
                result_sha=result_sha, read_paths=[], write_paths=[])
        self.assertEqual(raised.exception.completion_code, 'TEST_SCOPE_MISSING')

    def test_mixed_docs_and_code_without_registered_test_stays_missing(self):
        (self.root / 'docs').mkdir()
        (self.root / 'docs' / 'guide.md').write_text('Documentation.\n')
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'module.py').write_text('VALUE = 1\n')
        self._git('add', 'docs/guide.md', 'src/module.py')
        self._git('commit', '-m', 'mixed result')
        result_sha = self._git('rev-parse', 'HEAD')
        with self.assertRaises(LLMToolError) as raised:
            task_completion.run_bounded_tests(
                root=str(self.root), source_sha=self.source_sha, result_sha=result_sha,
                read_paths=[], write_paths=[])
        self.assertEqual(raised.exception.completion_code, 'TEST_SCOPE_MISSING')

    # #1169: bounded tests count only Developer-attributed paths when the
    # authenticated main pin is supplied.
    _MAIN_CODE_PATH = 'tools/llmtool/llmtool/from_main.py'

    def _trusted_main_with_code(self, *, extra=None):
        """Commit a code file (and optional extra files) on a trusted main branch."""
        self._git('branch', 'main', self.source_sha)
        self._git('checkout', 'main')
        files = {self._MAIN_CODE_PATH: 'VALUE = 1\n', **(extra or {})}
        for path, content in files.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self._git('add', *files)
        self._git('commit', '-m', 'main code change')
        main_sha = self._git('rev-parse', 'HEAD')
        self._git('checkout', 'feat/executor')
        return main_sha

    def _commit_paths(self, files, message):
        for path, content in files.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self._git('add', *files)
        self._git('commit', '-m', message)
        return self._git('rev-parse', 'HEAD')

    def _bounded_tests(self, result_sha, *, read_paths=(), trusted_base_sha=None):
        return task_completion.run_bounded_tests(
            root=str(self.root), source_sha=self.source_sha, result_sha=result_sha,
            read_paths=list(read_paths), write_paths=[],
            **({'trusted_base_sha': trusted_base_sha}
               if trusted_base_sha is not None else {}))

    def _assert_bounded_stop(self, code, result_sha, **kwargs):
        with self.assertRaises(LLMToolError) as raised:
            self._bounded_tests(result_sha, **kwargs)
        self.assertEqual(raised.exception.completion_code, code)

    def test_docs_only_task_stays_docs_only_after_clean_trusted_main_merge(self):
        main_sha = self._trusted_main_with_code()
        self._commit_paths({'docs/guide.md': 'Documentation.\n'}, 'developer docs')
        self._git('merge', '--no-ff', 'main', '-m', 'merge trusted main')
        result_sha = self._git('rev-parse', 'HEAD')
        self.assertIn(self._MAIN_CODE_PATH,
                      self._git('diff', '--name-only', self.source_sha, result_sha))

        evidence = self._bounded_tests(result_sha, trusted_base_sha=main_sha)
        self.assertEqual(evidence, [{'status': 'NOT_APPLICABLE_DOCS_ONLY',
                                     'changed_paths': ['docs/guide.md'],
                                     'source_sha': result_sha, 'docs_only': True}])
        # Control: without the pin the plain diff still decides, as before.
        self._assert_bounded_stop('TEST_SCOPE_MISSING', result_sha)

    def test_developer_code_change_still_needs_registered_scope_with_trusted_main(self):
        # The alone case runs first, so the main branch is created only once.
        for merge_main in (False, True):
            with self.subTest(merge_main=merge_main):
                self._git('reset', '--hard', self.source_sha)
                trusted = (self._trusted_main_with_code() if merge_main
                           else self.source_sha)
                target = self.root / self.source_path
                self._commit_paths({self.source_path: target.read_text() +
                                    '# developer change\n'}, 'developer code')
                if merge_main:
                    self._git('merge', '--no-ff', 'main', '-m', 'merge trusted main')
                result_sha = self._git('rev-parse', 'HEAD')
                self._assert_bounded_stop('TEST_SCOPE_MISSING', result_sha,
                                          trusted_base_sha=trusted)
                evidence = self._bounded_tests(result_sha, read_paths=[self.source_path],
                                               trusted_base_sha=trusted)
                self.assertEqual([item['path'] for item in evidence], [self.source_path])
                self.assertEqual(evidence[0]['status'], 'PASSED')
                self.assertEqual(evidence[0]['source_sha'], result_sha)

    def test_untrusted_second_parent_merge_paths_still_count(self):
        main_sha = self._trusted_main_with_code(extra={'README.md': 'Main docs.\n'})
        self._git('checkout', '-b', 'side-branch', self.source_sha)
        self._commit_paths({'tools/llmtool/llmtool/side.py': 'VALUE = 2\n'}, 'side code')
        self._git('checkout', 'feat/executor')
        self._commit_paths({'docs/guide.md': 'Documentation.\n'}, 'developer docs')
        self._git('merge', '--no-ff', 'side-branch', '-m', 'merge untrusted branch')
        result_sha = self._git('rev-parse', 'HEAD')
        self._assert_bounded_stop('TEST_SCOPE_MISSING', result_sha,
                                  trusted_base_sha=main_sha)

    def test_hand_resolved_trusted_main_merge_paths_still_count(self):
        main_sha = self._trusted_main_with_code()
        self._commit_paths({'docs/guide.md': 'Documentation.\n'}, 'developer docs')
        self._git('merge', '--no-ff', '--no-commit', 'main')
        (self.root / self._MAIN_CODE_PATH).write_text('VALUE = 99\n')
        self._git('add', self._MAIN_CODE_PATH)
        self._git('commit', '-m', 'merge trusted main with a hand edit')
        result_sha = self._git('rev-parse', 'HEAD')
        self.assertEqual(len(self._git('rev-list', '--parents', '-n', '1',
                                       result_sha).split()), 3)
        self._assert_bounded_stop('TEST_SCOPE_MISSING', result_sha,
                                  trusted_base_sha=main_sha)

    def test_conflicted_trusted_main_merge_paths_still_count(self):
        target = self.root / self.source_path
        original = target.read_text()
        main_sha = self._trusted_main_with_code(
            extra={self.source_path: original + '# main side\n'})
        self._commit_paths({self.source_path: original + '# developer side\n'},
                           'developer code')
        merge = subprocess.run(['/usr/bin/git', 'merge', '--no-ff', 'main', '-m',
                                'merge trusted main'], cwd=self.root, text=True,
                               capture_output=True)
        self.assertNotEqual(merge.returncode, 0, merge.stdout + merge.stderr)
        # Resolve to trusted main's version; the conflict path still counts.
        self._git('checkout', '--theirs', self.source_path)
        self._git('add', self.source_path)
        self._git('commit', '-m', 'resolve to main')
        result_sha = self._git('rev-parse', 'HEAD')
        self.assertEqual(self._git('rev-parse', result_sha + ':' + self.source_path),
                         self._git('rev-parse', main_sha + ':' + self.source_path))
        self._assert_bounded_stop('TEST_SCOPE_MISSING', result_sha,
                                  trusted_base_sha=main_sha)

    def test_empty_developer_paths_stop_as_test_path_invalid(self):
        with self.subTest(case='no commits'):
            self._assert_bounded_stop('TEST_PATH_INVALID', self.source_sha,
                                      trusted_base_sha=self.source_sha)
        with self.subTest(case='only a clean trusted main merge'):
            main_sha = self._trusted_main_with_code()
            self._git('merge', '--no-ff', 'main', '-m', 'merge trusted main')
            result_sha = self._git('rev-parse', 'HEAD')
            self._assert_bounded_stop('TEST_PATH_INVALID', result_sha,
                                      trusted_base_sha=main_sha)
            # Control: the plain diff sees main's code file.
            self._assert_bounded_stop('TEST_SCOPE_MISSING', result_sha)

    def test_github_review_requires_d6_actor_session_and_live_exact_head(self):
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer'
        self._git('worktree', 'add', '-b', 'review/branch', str(reviewer_root), result_sha)
        reviewer_head = result_sha
        self._write_registration('reviewer-task', 'claude-reviewer', 'Reviewer session',
            reviewer_root, 'review/branch', reviewer_head, reviewer_head,
            native_session_id='native-reviewer', github_login='reviewer-user',
            state='REVIEW_PENDING')
        pr = {'state': 'open', 'merged': False,
              'head': {'sha': result_sha}, 'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        binding_line = self._review_binding(head=result_sha)
        review = [{'state': 'APPROVED', 'commit_id': result_sha,
                   'user': {'login': 'reviewer-user'},
                   'submitted_at': '2026-10-21T10:00:00Z',
                   'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' + binding_line}]
        checks = {'total_count': 1, 'check_runs': [
            {'id': i, 'name': name, 'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success'}
            for i, name in enumerate(('check (macOS 3.13)',), 1)]}
        api = self._github_response_sequence(result_sha, pr, review, checks)
        with patch('llmtool.task_completion._github_get', side_effect=api):
            evidence = task_completion.collect_github_evidence(**self._github_kwargs(result_sha))
        self.assertEqual(evidence['evidence']['formal_review_state'], 'ACCEPTED')
        self.assertEqual(evidence['evidence']['review_task_id'], '1' * 32)
        self.assertTrue(evidence['evidence']['review_binding_verified'])
        self.assertEqual(evidence['evidence']['required_checks'], [
            'check (Linux 3.14)', 'check (macOS 3.13)'])
        self.assertEqual(evidence['evidence']['not_run_checks'], [
            {'name': 'Linux Python 3.9', 'status': 'NOT_RUN'},
            {'name': 'Windows CI', 'status': 'NOT_RUN'}])
        self.assertEqual(evidence['missing_gates'], [])
        with patch('llmtool.task_completion._github_get',
                   side_effect=self._github_response_sequence(result_sha, pr, review, checks)):
            unknown_executor_native = task_completion.collect_github_evidence(
                **self._github_kwargs(result_sha, executor_native_session_id=None))
        self.assertIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT', unknown_executor_native['missing_gates'])
        superseded_review = review + [{'state': 'CHANGES_REQUESTED',
            'commit_id': result_sha, 'user': {'login': 'reviewer-user'},
            'submitted_at': '2026-10-21T11:00:00Z',
            'body': 'Changes requested'}]
        with patch('llmtool.task_completion._github_get', side_effect=self._github_response_sequence(
                result_sha, pr, superseded_review, checks)):
            rejected = task_completion.collect_github_evidence(**self._github_kwargs(result_sha))
        self.assertEqual(rejected['evidence']['formal_review_state'], 'UNVERIFIED')
        self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', rejected['missing_gates'])

    def test_advance_dispatched_formal_pass_comment_satisfies_review_gate(self):
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer'
        self._git('worktree', 'add', '-b', 'review/comment-branch',
                  str(reviewer_root), result_sha)
        self._write_registration('reviewer-task', 'claude-reviewer',
            'native-reviewer-session', reviewer_root, 'review/comment-branch',
            result_sha, result_sha, native_session_id='native-reviewer-session',
            github_login='reviewer-user', state='REVIEW_PENDING')
        pr = {'state': 'open', 'merged': False, 'head': {'sha': result_sha},
              'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        comment_body = (
            '<!-- activity: type=review_verdict actor=Claude target=PM status=PASS -->\n'
            f'Verdict: PASS | Version: v1 | HEAD: {result_sha}\n'
            'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' +
            self._review_binding(head=result_sha))
        comments = [{'id': 42, 'user': {'login': 'reviewer-user'},
            'body': comment_body, 'created_at': '2026-10-21T10:00:00Z',
            'updated_at': '2026-10-21T10:00:00Z'}]
        checks = {'total_count': 1, 'check_runs': [
            {'id': 1, 'name': 'check (macOS 3.13)', 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success'}]}
        api = self._github_response_sequence(result_sha, pr, [], checks,
                                             comments=comments)
        with patch('llmtool.task_completion._github_get', side_effect=api):
            evidence = task_completion.collect_github_evidence(
                **self._github_kwargs(result_sha))
        self.assertEqual(evidence['evidence']['review_source'], 'PR_COMMENT')
        self.assertEqual(evidence['evidence']['formal_review_state'], 'ACCEPTED')
        self.assertEqual(evidence['evidence']['reviewer_github_login'], 'reviewer-user')
        self.assertEqual(evidence['evidence']['reviewer_session_ref'],
                         'native-reviewer-session')
        self.assertEqual(evidence['missing_gates'], [])

    def _company_gw_review_fixture(self, reviewer='codex-reviewer'):
        """Same PR, bound APPROVED review and checks as the D6 actor-session test."""
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer'
        self._git('worktree', 'add', '-b', 'review/branch', str(reviewer_root), result_sha)
        pr = {'state': 'open', 'merged': False,
              'head': {'sha': result_sha}, 'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        review = [{'state': 'APPROVED', 'commit_id': result_sha,
                   'user': {'login': 'reviewer-user'},
                   'submitted_at': '2026-10-21T10:00:00Z',
                   'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' +
                           self._review_binding(head=result_sha)}]
        checks = {'total_count': 1, 'check_runs': [
            {'id': 1, 'name': 'check (macOS 3.13)', 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success'}]}

        def register(identity):
            self._write_registration('reviewer-task', identity, 'Reviewer session',
                reviewer_root, 'review/branch', result_sha, result_sha,
                native_session_id='native-reviewer', github_login='reviewer-user',
                state='REVIEW_PENDING')

        def collect(task_record, **overrides):
            kwargs = {'executor_identity': 'company-gw',
                      'executor_native_session_id': None,
                      'task_record': task_record}
            kwargs.update(overrides)
            with patch('llmtool.task_completion._github_get',
                       side_effect=self._github_response_sequence(
                           result_sha, pr, review, checks)):
                return task_completion.collect_github_evidence(
                    **self._github_kwargs(result_sha, **kwargs))

        register(reviewer)
        return register, collect

    @staticmethod
    def _company_gw_task_record(*, pm_session_id='pm-session-synthetic', **developer):
        record = {'task_id': '1' * 32, 'run_id': '2' * 32,
                  'pm_session_id': pm_session_id,
                  'developer': {
                      'provider_profile': 'company-gw',
                      'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
                      'state': 'DEVELOPER_RESPONDED',
                      'artifact_format': 'unified_diff',
                      'artifact_sha256': hashlib.sha256(
                          b'synthetic unified diff\n').hexdigest()}}
        record['developer'].update(developer)
        return record

    def test_company_gw_text_proposal_accepts_missing_executor_native_session(self):
        _, collect = self._company_gw_review_fixture()
        for executor_native in (None, 'UNKNOWN'):
            with self.subTest(executor_native=executor_native):
                evidence = collect(self._company_gw_task_record(),
                                   executor_native_session_id=executor_native)
                self.assertNotIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT',
                                 evidence['missing_gates'])
                self.assertEqual(evidence['evidence']['formal_review_state'], 'ACCEPTED')
                self.assertEqual(evidence['missing_gates'], [])

    def test_company_gw_text_proposal_keeps_family_and_pm_session_gates(self):
        register, collect = self._company_gw_review_fixture(reviewer='claude-reviewer')
        same_family = collect(self._company_gw_task_record(model='claude-sonnet-synthetic'))
        self.assertIn('REVIEW_DEVELOPER_FAMILY_UNSUPPORTED', same_family['missing_gates'])
        self.assertEqual(same_family['evidence']['formal_review_state'], 'UNVERIFIED')
        register('codex-reviewer')
        same_family = collect(self._company_gw_task_record(model='gpt-synthetic'))
        self.assertIn('REVIEW_DEVELOPER_FAMILY_UNSUPPORTED', same_family['missing_gates'])
        self.assertEqual(same_family['evidence']['formal_review_state'], 'UNVERIFIED')
        pm_is_reviewer = collect(self._company_gw_task_record(pm_session_id='native-reviewer'))
        self.assertIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT',
                      pm_is_reviewer['missing_gates'])
        self.assertEqual(pm_is_reviewer['evidence']['formal_review_state'], 'UNVERIFIED')
        same_executor = collect(self._company_gw_task_record(),
                                executor_native_session_id='native-reviewer')
        self.assertIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT',
                      same_executor['missing_gates'])
        d6_not_independent = collect(self._company_gw_task_record(),
                                     executor_session_ref='Reviewer session')
        self.assertIn('REVIEWER_D6_SESSION_NOT_INDEPENDENT',
                      d6_not_independent['missing_gates'])
        self.assertEqual(d6_not_independent['evidence']['formal_review_state'], 'UNVERIFIED')

    def test_company_gw_pm_session_gate_compares_normalized_native_identity(self):
        # A UUID-backed PM session is stored as `pm-<native>` on the Task while
        # the Reviewer D6 holds the raw native id; the gate must still match.
        _, collect = self._company_gw_review_fixture()
        for pm_session in ('pm-native-reviewer', 'PM-NATIVE-REVIEWER',
                           'pm-Native-Reviewer', ' pm-native-reviewer ',
                           'NATIVE-REVIEWER'):
            with self.subTest(pm_session=pm_session):
                evidence = collect(self._company_gw_task_record(pm_session_id=pm_session))
                self.assertIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT',
                              evidence['missing_gates'])
                self.assertEqual(evidence['evidence']['formal_review_state'], 'UNVERIFIED')
        for pm_session in ('pm-', 'pm-UNKNOWN', 'unknown', ''):
            with self.subTest(pm_session=pm_session):
                evidence = collect(self._company_gw_task_record(pm_session_id=pm_session))
                self.assertIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT',
                              evidence['missing_gates'])
                self.assertEqual(evidence['evidence']['formal_review_state'], 'UNVERIFIED')
        with self.subTest(pm_session='pm-native-pm-synthetic'):
            evidence = collect(self._company_gw_task_record(
                pm_session_id='pm-native-pm-synthetic'))
            self.assertNotIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT',
                             evidence['missing_gates'])
            self.assertEqual(evidence['evidence']['formal_review_state'], 'ACCEPTED')
            self.assertEqual(evidence['missing_gates'], [])

    def test_missing_executor_native_session_stays_not_independent_off_company_gw(self):
        _, collect = self._company_gw_review_fixture()
        cases = {
            'claude': self._company_gw_task_record(provider_profile='claude'),
            'agy': self._company_gw_task_record(provider_profile='agy',
                                                model='gemini-synthetic'),
            'opencode': self._company_gw_task_record(provider_profile='opencode'),
            'codex': self._company_gw_task_record(provider_profile='codex'),
            'gw-agent': self._company_gw_task_record(provider_profile='gw-agent'),
            'company-gw-agent': self._company_gw_task_record(
                provider_profile='company-gw-agent'),
            'missing artifact_sha256': self._company_gw_task_record(artifact_sha256=None),
            'short artifact_sha256': self._company_gw_task_record(artifact_sha256='a' * 40),
            'other backend': self._company_gw_task_record(
                backend_session_semantics='CLI_REPORTED_SESSION_ID'),
            'not responded': self._company_gw_task_record(state='DEVELOPER_PENDING'),
            'other artifact format': self._company_gw_task_record(artifact_format='text'),
            'missing pm session': self._company_gw_task_record(pm_session_id=None),
        }
        for name, record in cases.items():
            with self.subTest(case=name):
                evidence = collect(record)
                self.assertIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT',
                              evidence['missing_gates'])
                self.assertEqual(evidence['evidence']['formal_review_state'], 'UNVERIFIED')
        with self.subTest(case='other executor identity'):
            evidence = collect(self._company_gw_task_record(),
                               executor_identity='executor-agent')
            self.assertIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT', evidence['missing_gates'])
            self.assertEqual(evidence['evidence']['formal_review_state'], 'UNVERIFIED')

    def test_codex_formal_pass_requires_claude_task_and_completed_claim(self):
        result_sha, _ = self._make_result()
        pr = {'state': 'open', 'merged': False, 'head': {'sha': result_sha},
              'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        checks = {'total_count': 1, 'check_runs': [
            {'id': 1, 'name': 'check (macOS 3.13)', 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success'}]}
        comment_body = (
            '<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n'
            f'Verdict: PASS | Version: v1 | HEAD: {result_sha}\n'
            'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' +
            self._review_binding(head=result_sha))
        comment = {'id': 44, 'user': {'login': 'reviewer-user'},
                   'body': comment_body, 'created_at': '2026-10-21T10:00:00Z',
                   'updated_at': '2026-10-21T10:00:00Z'}

        case_number = [0]

        def evaluate(*, reviewer='codex-reviewer', author='claude',
                     review_text=comment_body, binding_body=comment_body,
                     task_digest='3' * 64):
            case_number[0] += 1
            reviewer_root = self.base / ('reviewer-' + str(case_number[0]))
            reviewer_branch = 'review/' + reviewer + '-' + author + '-' + str(case_number[0])
            self._git('worktree', 'add', '-b', reviewer_branch,
                      str(reviewer_root), result_sha)
            self._write_registration('reviewer-task', reviewer,
                'native-reviewer-session', reviewer_root, reviewer_branch,
                result_sha, result_sha, native_session_id='native-reviewer-session',
                github_login='reviewer-user', state='REVIEW_PENDING')
            current_comment = dict(comment, body=binding_body)
            task = {'task_id': '1' * 32, 'run_id': '2' * 32,
                    'task_binding': {'contract_digest': task_digest},
                    'developer': {'provider_profile': author},
                    'review_dispatch_claim': [{
                        'pr': 10, 'head_sha': result_sha, 'reviewer_task': 'reviewer-task',
                        'version': 1, 'state': 'COMPLETED',
                        'review_result': {'task_id': '1' * 32, 'run_id': '2' * 32,
                            'contract_digest': task_digest, 'head': result_sha,
                            'verdict': 'PASS', 'review_text': review_text}}]}
            api = self._github_response_sequence(result_sha, pr, [], checks,
                                                 comments=[current_comment])
            with patch('llmtool.task_store.TaskStore') as task_store:
                task_store.return_value.get.return_value = task
                with patch('llmtool.task_completion._github_get', side_effect=api):
                    return task_completion.collect_github_evidence(
                        **self._github_kwargs(result_sha, task_record=task))

        accepted = evaluate()
        self.assertEqual(accepted['evidence']['formal_review_state'], 'ACCEPTED')
        self.assertEqual(accepted['missing_gates'], [])
        self.assertEqual(evaluate(author='codex')['evidence']['formal_review_state'], 'UNVERIFIED')
        self.assertEqual(evaluate(reviewer='claude-reviewer')['evidence']['formal_review_state'],
                         'UNVERIFIED')
        self.assertEqual(evaluate(binding_body=comment_body.replace('task=' + '1' * 32,
                           'task=' + '9' * 32))['evidence']['formal_review_state'], 'UNVERIFIED')
        self.assertEqual(evaluate(review_text=comment_body + '\nDifferent text')
                         ['evidence']['formal_review_state'], 'UNVERIFIED')
        mismatched_contract_body = comment_body.replace(
            'contract=' + '3' * 64, 'contract=' + '4' * 64)
        self.assertEqual(evaluate(task_digest='4' * 64,
                                  binding_body=mismatched_contract_body,
                                  review_text=mismatched_contract_body)
                         ['evidence']['formal_review_state'], 'UNVERIFIED')

    def test_approved_review_family_matrix_fails_closed(self):
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer-family-matrix'
        self._git('worktree', 'add', '-b', 'review/family-matrix',
                  str(reviewer_root), result_sha)
        pr = {'state': 'open', 'merged': False, 'head': {'sha': result_sha},
              'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        checks = {'total_count': 1, 'check_runs': [
            {'id': 1, 'name': 'check (macOS 3.13)', 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success'}]}
        profiles = ('claude', 'codex', 'agy', 'opencode', 'company-gw', 'gw-agent',
                    'company-gw-agent', None, 'unrecognized')
        reviewers = ('claude-reviewer', 'codex-reviewer', 'other-reviewer')
        for profile in profiles:
            for reviewer in reviewers:
                with self.subTest(profile=profile, reviewer=reviewer):
                    self._write_registration(
                        'reviewer-task', reviewer, 'native-reviewer-session',
                        reviewer_root, 'review/family-matrix', result_sha, result_sha,
                        native_session_id='native-reviewer-session',
                        github_login='reviewer-user', state='REVIEW_PENDING')
                    review = [{'state': 'APPROVED', 'commit_id': result_sha,
                               'user': {'login': 'reviewer-user'},
                               'submitted_at': '2026-10-21T10:00:00Z',
                               'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' +
                                       self._review_binding(head=result_sha)}]
                    developer = {'provider_profile': profile}
                    if profile == 'agy':
                        developer['model'] = 'gemini-2.5-pro'
                    task = ({} if profile is None else {'developer': developer})
                    api = self._github_response_sequence(result_sha, pr, review, checks)
                    with patch('llmtool.task_completion._github_get', side_effect=api):
                        evidence = task_completion.collect_github_evidence(
                            **self._github_kwargs(result_sha, task_record=task))
                    expected = (profile in ('agy', 'opencode', 'company-gw', 'gw-agent',
                                             'company-gw-agent') and
                                reviewer in ('claude-reviewer', 'codex-reviewer')) or (
                                profile == 'claude' and reviewer == 'codex-reviewer') or (
                                profile == 'codex' and reviewer == 'claude-reviewer')
                    self.assertEqual(evidence['evidence']['formal_review_state'],
                                     'ACCEPTED' if expected else 'UNVERIFIED')
                    if not expected:
                        self.assertIn('REVIEW_DEVELOPER_FAMILY_UNSUPPORTED',
                                      evidence['missing_gates'])

    def test_pr_comment_family_matrix_matches_formal_verdict_classifier(self):
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer-comment-family-matrix'
        self._git('worktree', 'add', '-b', 'review/comment-family-matrix',
                  str(reviewer_root), result_sha)
        pr = {'state': 'open', 'merged': False, 'head': {'sha': result_sha},
              'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        checks = {'total_count': 1, 'check_runs': [
            {'id': 1, 'name': 'check (macOS 3.13)', 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success'}]}
        profiles = ('claude', 'codex', 'agy', 'opencode', 'company-gw', 'gw-agent',
                    'company-gw-agent', None, 'unrecognized')
        for profile in profiles:
            for reviewer in ('claude-reviewer', 'codex-reviewer', 'other-reviewer'):
                with self.subTest(profile=profile, reviewer=reviewer):
                    self._write_registration(
                        'reviewer-task', reviewer, 'native-reviewer-session',
                        reviewer_root, 'review/comment-family-matrix', result_sha,
                        result_sha, native_session_id='native-reviewer-session',
                        github_login='reviewer-user', state='REVIEW_PENDING')
                    actor = 'Claude' if reviewer == 'claude-reviewer' else 'Codex'
                    binding = self._review_binding(head=result_sha)
                    body = (f'<!-- activity: type=review_verdict actor={actor} target=PM status=PASS -->\n'
                            f'Verdict: PASS | Version: v1 | HEAD: {result_sha}\n'
                            'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' + binding)
                    task = {'task_id': '1' * 32, 'run_id': '2' * 32,
                            'task_binding': {'contract_digest': '3' * 64}}
                    if profile is not None:
                        developer = {'provider_profile': profile}
                        if profile == 'agy':
                            developer['model'] = 'gemini-2.5-pro'
                        task['developer'] = developer
                    if reviewer == 'codex-reviewer':
                        task['review_dispatch_claim'] = [{
                            'pr': 10, 'head_sha': result_sha,
                            'reviewer_task': 'reviewer-task', 'version': 1,
                            'state': 'COMPLETED', 'review_result': {
                                'task_id': task['task_id'], 'run_id': task['run_id'],
                                'contract_digest': '3' * 64, 'head': result_sha,
                                'verdict': 'PASS', 'review_text': body}}]
                    api = self._github_response_sequence(
                        result_sha, pr, [], checks, comments=[{
                            'id': 51, 'user': {'login': 'reviewer-user'}, 'body': body,
                            'created_at': '2026-10-21T10:00:00Z',
                            'updated_at': '2026-10-21T10:00:00Z'}])
                    with patch('llmtool.task_completion._github_get', side_effect=api):
                        evidence = task_completion.collect_github_evidence(
                            **self._github_kwargs(result_sha, task_record=task))
                    expected = (profile in ('agy', 'opencode', 'company-gw', 'gw-agent',
                                             'company-gw-agent') and
                                reviewer in ('claude-reviewer', 'codex-reviewer') or
                                profile == 'claude' and reviewer == 'codex-reviewer' or
                                profile == 'codex' and reviewer == 'claude-reviewer')
                    self.assertEqual(evidence['evidence']['formal_review_state'],
                                     'ACCEPTED' if expected else 'UNVERIFIED')
                    if not expected:
                        self.assertIn('REVIEW_DEVELOPER_FAMILY_UNSUPPORTED',
                                      evidence['missing_gates'])

    def test_agy_review_family_uses_actual_model_and_unknown_fails_closed(self):
        for model, compatible_reviewers in (
                ('claude-sonnet-5-5-medium', ('codex-reviewer',)),
                ('gemini-2.5-pro', ('claude-reviewer', 'codex-reviewer'))):
            developer = {'provider_profile': 'agy', 'model': model}
            with self.subTest(model=model):
                for reviewer in ('claude-reviewer', 'codex-reviewer'):
                    self.assertEqual(task_completion._review_family_compatible(
                        developer, reviewer), reviewer in compatible_reviewers)
        for developer in ({'provider_profile': 'agy'},
                          {'provider_profile': 'agy', 'model': 'other-model'}):
            with self.subTest(developer=developer):
                for reviewer in ('claude-reviewer', 'codex-reviewer'):
                    self.assertFalse(task_completion._review_family_compatible(
                        developer, reviewer))

    def test_review_family_uses_recorded_run_model_and_conflicts_fail_closed(self):
        missing = object()
        deepseek = 'opencode/deepseek-v4.1-flash-free#high'
        cases = []
        for profile in ('opencode', 'company-gw', 'company-gw-agent', 'gw-agent'):
            cases.extend((profile, model, family) for model, family in (
                (deepseek, 'other'),
                ('anthropic/claude-opus-5-5', 'claude'),
                ('claude-opus-5-5', 'claude'),
                ('Anthropic/Claude-Sonnet-5-5#high', 'claude'),
                ('openai/gpt-6-luna', 'codex'),
                ('gpt-6-luna', 'codex'),
                ('openai/codex-mini#high', 'codex'),
                ('o4-mini', 'codex'),
                ('o3', 'codex'),
                ('other-model', 'other'),
                ('opus-5', 'other'),
                (None, 'other'),
                (123, 'other'),
                (missing, 'other')))
        cases.extend(('codex', model, family) for model, family in (
            ('gpt-6-luna', 'codex'),
            ('openai/gpt-6-luna', 'codex'),
            ('o4-mini', 'codex'),
            (deepseek, 'codex'),
            ('claude-opus-5-5', None),
            ('anthropic/claude-opus-5-5', None),
            (None, 'codex'),
            (missing, 'codex')))
        cases.extend(('claude', model, family) for model, family in (
            ('claude-opus-5-5', 'claude'),
            ('anthropic/claude-opus-5-5', 'claude'),
            (deepseek, 'claude'),
            ('gpt-6-luna', None),
            ('openai/gpt-6-luna', None),
            ('codex-mini', None),
            ('o4-mini', None),
            (None, 'claude'),
            (missing, 'claude')))
        cases.extend(('agy', model, family) for model, family in (
            ('claude-sonnet-5-5-medium', 'claude'),
            ('gemini-2.5-pro', 'other'),
            ('anthropic/claude-opus-5-5', None),
            ('gpt-6-luna', None),
            ('o4-mini', None),
            (deepseek, None),
            (None, None),
            (missing, None)))
        cases.extend(('unknown-route', model, None) for model in (
            'claude-opus-5-5', 'gpt-6-luna', deepseek, missing))
        compatible = {
            'claude': ('codex-reviewer',),
            'codex': ('claude-reviewer',),
            'other': ('claude-reviewer', 'codex-reviewer'),
            None: (),
        }
        for profile, model, family in cases:
            developer = {'provider_profile': profile}
            if model is not missing:
                developer['model'] = model
            with self.subTest(developer=developer):
                self.assertEqual(
                    task_completion._reviewer_author_family(developer), family)
                for reviewer in ('claude-reviewer', 'codex-reviewer'):
                    self.assertEqual(task_completion._review_family_compatible(
                        developer, reviewer), reviewer in compatible[family])
        for developer in (None, 'opencode', ['claude-opus-5-5']):
            with self.subTest(developer=developer):
                self.assertIsNone(task_completion._reviewer_author_family(developer))
                for reviewer in ('claude-reviewer', 'codex-reviewer'):
                    self.assertFalse(task_completion._review_family_compatible(
                        developer, reviewer))

    def test_review_binding_must_match_task_run_contract_and_result_head(self):
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer'
        self._git('worktree', 'add', '-b', 'review/branch', str(reviewer_root), result_sha)
        self._write_registration('reviewer-task', 'review-agent', 'Reviewer D6 session',
            reviewer_root, 'review/branch', result_sha, result_sha,
            native_session_id='native-reviewer', github_login='reviewer-user')
        pr = {'state': 'open', 'merged': False, 'head': {'sha': result_sha},
              'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        wrong = self._review_binding(run_id='9' * 32, head=result_sha)
        reviews = [{'state': 'APPROVED', 'commit_id': result_sha,
                    'user': {'login': 'reviewer-user'},
                    'submitted_at': '2026-10-21T10:00:00Z',
                    'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' + wrong}]
        checks = {'total_count': 1, 'check_runs': [
            {'id': i, 'name': name, 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success'}
            for i, name in enumerate(('check (macOS 3.13)',), 1)]}
        with patch('llmtool.task_completion._github_get', side_effect=self._github_response_sequence(
                result_sha, pr, reviews, checks)):
            result = task_completion.collect_github_evidence(**self._github_kwargs(result_sha))
        self.assertIn('FORMAL_REVIEW_BINDING_MISMATCH', result['missing_gates'])
        self.assertNotEqual(result['evidence']['formal_review_state'], 'ACCEPTED')

    def test_same_reviewer_actor_and_truncated_reviews_fail_closed(self):
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer'
        self._git('worktree', 'add', '-b', 'review/branch', str(reviewer_root), result_sha)
        self._write_registration('reviewer-task', 'executor-agent', 'Executor D6 session',
            reviewer_root, 'review/branch', result_sha, result_sha,
            native_session_id='native-executor', github_login='author-user')
        pr = {'state': 'open', 'merged': False,
              'head': {'sha': result_sha}, 'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        with patch('llmtool.task_completion._github_get', side_effect=[pr, [{}] * 100]):
            with self.assertRaises(LLMToolError):
                task_completion.collect_github_evidence(**self._github_kwargs(result_sha))

    def test_distinct_d6_reviewer_can_share_pr_author_login_and_unknown_native_session(self):
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer'
        self._git('worktree', 'add', '-b', 'review/branch', str(reviewer_root), result_sha)
        self._write_registration('reviewer-task', 'review-agent', 'Reviewer D6 session',
            reviewer_root, 'review/branch', result_sha, result_sha,
            native_session_id='UNKNOWN', github_login='author-user')
        pr = {'state': 'open', 'merged': False,
              'head': {'sha': result_sha}, 'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        review = [{'state': 'APPROVED', 'commit_id': result_sha,
                   'user': {'login': 'author-user'},
                   'submitted_at': '2026-10-21T10:00:00Z',
                   'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' +
                           self._review_binding(head=result_sha)}]
        checks = {'total_count': 1, 'check_runs': [
            {'id': i, 'name': name, 'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success'}
            for i, name in enumerate(('check (macOS 3.13)',), 1)]}
        with patch('llmtool.task_completion._github_get', side_effect=self._github_response_sequence(
                result_sha, pr, review, checks)):
            result = task_completion.collect_github_evidence(**self._github_kwargs(
                result_sha, executor_native_session_id=None))
        self.assertEqual(result['evidence']['formal_review_state'], 'UNVERIFIED')
        self.assertEqual(result['evidence']['review_session_identity'], 'UNKNOWN')
        self.assertIn('REVIEWER_NATIVE_SESSION_UNVERIFIED', result['missing_gates'])

    def test_merged_pr_collects_empty_pull_request_ci_runs(self):
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer-merged'
        self._git('worktree', 'add', '-b', 'review/merged-branch',
                  str(reviewer_root), result_sha)
        self._write_registration('reviewer-task', 'claude-reviewer', 'Reviewer session',
            reviewer_root, 'review/merged-branch', result_sha, result_sha,
            native_session_id='native-reviewer', github_login='reviewer-user',
            state='REVIEW_PENDING')
        pr = {'state': 'closed', 'merged': True, 'head': {
                  'sha': result_sha, 'ref': 'feature/merged'},
              'base': {'ref': 'main', 'repo': {
                  'full_name': 'davidcatcat/davidcattoolsproject'}},
              'merge_commit_sha': 'b' * 40, 'user': {'login': 'author-user'}}
        reviews = [{'state': 'APPROVED', 'commit_id': result_sha,
                    'user': {'login': 'reviewer-user'},
                    'submitted_at': '2026-10-28T10:00:00Z',
                    'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' +
                            self._review_binding(head=result_sha)}]
        checks = {'total_count': 1, 'check_runs': [
            {'id': 1, 'name': 'check (macOS 3.13)', 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success'}]}
        api = self._github_response_sequence(result_sha, pr, reviews, checks)
        for run_index in (4, 8):
            run = api[run_index]['workflow_runs'][0]
            run['pull_requests'] = []
            run['head_branch'] = 'feature/merged'
            run['head_repository'] = {
                'full_name': 'davidcatcat/davidcattoolsproject'}
        with patch('llmtool.task_completion._github_get', side_effect=api):
            evidence = task_completion.collect_github_evidence(
                **self._github_kwargs(
                    result_sha,
                    task_record={'developer': {'provider_profile': 'codex'}}))
        self.assertEqual(evidence['missing_gates'], [])
        self.assertEqual(evidence['evidence']['pr_state'], 'MERGED')
        self.assertEqual(evidence['evidence']['merge_commit_sha'], 'b' * 40)

    def test_merged_pr_with_different_head_sha_stops_pr_head_mismatch(self):
        result_sha = 'a' * 40
        pr = {'state': 'closed', 'merged': True,
              'head': {'sha': 'c' * 40, 'ref': 'feature/merged'},
              'base': {'ref': 'main', 'repo': {
                  'full_name': 'davidcatcat/davidcattoolsproject'}},
              'merge_commit_sha': 'b' * 40, 'user': {'login': 'author-user'}}
        with patch('llmtool.task_completion._github_get', return_value=pr):
            with self.assertRaises(LLMToolError) as raised:
                task_completion.collect_github_evidence(**self._github_kwargs(result_sha))
        self.assertEqual(raised.exception.completion_code, 'PR_HEAD_MISMATCH')

    def test_closed_unmerged_pr_stops_pr_head_mismatch(self):
        result_sha = 'a' * 40
        pr = {'state': 'closed', 'merged': False,
              'head': {'sha': result_sha, 'ref': 'feature/closed'},
              'base': {'ref': 'main', 'repo': {
                  'full_name': 'davidcatcat/davidcattoolsproject'}},
              'merge_commit_sha': None, 'user': {'login': 'author-user'}}
        with patch('llmtool.task_completion._github_get', return_value=pr):
            with self.assertRaises(LLMToolError) as raised:
                task_completion.collect_github_evidence(**self._github_kwargs(result_sha))
        self.assertEqual(raised.exception.completion_code, 'PR_HEAD_MISMATCH')

    def test_merged_empty_pull_request_run_requires_matching_head_branch(self):
        workflow_path = '.github/workflows/llmtool-mac-ci.yml'
        workflow = {'id': 42, 'path': workflow_path,
                    'name': 'llmtool Mac CI', 'state': 'active'}
        run = {'workflow_id': 42, 'path': workflow_path,
               'event': 'pull_request', 'head_sha': 'a' * 40,
               'head_branch': 'different-branch',
               'head_repository': {
                   'full_name': 'davidcatcat/davidcattoolsproject'},
               'pull_requests': []}
        runs = {'total_count': 1, 'workflow_runs': [run]}
        with patch('llmtool.task_completion._github_get', side_effect=[workflow, runs]):
            with self.assertRaises(LLMToolError) as raised:
                task_completion._collect_one_ci_workflow(
                    pr_number=10, result_sha='a' * 40, workflow_path=workflow_path,
                    workflow_name='llmtool Mac CI', check_name='check (macOS 3.13)',
                    runner_names=task_completion._CI_RUNNER_NAMES,
                    pr_merged=True, pr_head_ref='feature/merged')
        self.assertEqual(raised.exception.completion_code, 'CI_WORKFLOW_RUN_MISMATCH')

    def test_open_pr_empty_pull_request_run_still_fails_ci_workflow_run_mismatch(self):
        workflow_path = '.github/workflows/llmtool-mac-ci.yml'
        workflow = {'id': 42, 'path': workflow_path,
                    'name': 'llmtool Mac CI', 'state': 'active'}
        run = {'workflow_id': 42, 'path': workflow_path,
               'event': 'pull_request', 'head_sha': 'a' * 40,
               'head_branch': 'feature/open',
               'head_repository': {
                   'full_name': 'davidcatcat/davidcattoolsproject'},
               'pull_requests': []}
        runs = {'total_count': 1, 'workflow_runs': [run]}
        with patch('llmtool.task_completion._github_get', side_effect=[workflow, runs]):
            with self.assertRaises(LLMToolError) as raised:
                task_completion._collect_one_ci_workflow(
                    pr_number=10, result_sha='a' * 40, workflow_path=workflow_path,
                    workflow_name='llmtool Mac CI', check_name='check (macOS 3.13)',
                    runner_names=task_completion._CI_RUNNER_NAMES,
                    pr_merged=False, pr_head_ref='feature/open')
        self.assertEqual(raised.exception.completion_code, 'CI_WORKFLOW_RUN_MISMATCH')

    def test_pr_head_mismatch_fails_and_ci_head_mismatch_is_missing(self):
        result_sha, _ = self._make_result()
        pr = {'state': 'open', 'merged': False,
              'head': {'sha': '9' * 40}, 'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        with patch('llmtool.task_completion._github_get', return_value=pr):
            with self.assertRaises(LLMToolError):
                task_completion.collect_github_evidence(**self._github_kwargs(result_sha))
        reviewer_root = self.base / 'reviewer'
        self._git('worktree', 'add', '-b', 'review/branch', str(reviewer_root), result_sha)
        self._write_registration('reviewer-task', 'review-agent', 'Reviewer session',
            reviewer_root, 'review/branch', result_sha, result_sha,
            native_session_id='native-reviewer', github_login='reviewer-user')
        pr['head']['sha'] = result_sha
        reviews = [{'state': 'APPROVED', 'commit_id': result_sha,
                    'user': {'login': 'reviewer-user'},
                    'submitted_at': '2026-10-21T10:00:00Z',
                    'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' +
                            self._review_binding(head=result_sha)}]
        checks = {'total_count': 1, 'check_runs': [
            {'id': 1, 'name': 'check (macOS 3.13)', 'head_sha': '8' * 40,
             'status': 'completed', 'conclusion': 'success'}]}
        with patch('llmtool.task_completion._github_get', side_effect=self._github_response_sequence(
                result_sha, pr, reviews, checks)):
            evidence = task_completion.collect_github_evidence(**self._github_kwargs(result_sha))
        self.assertIn('REQUIRED_CI_MISSING', evidence['missing_gates'])

    def test_newer_pending_or_failed_check_defeats_historical_success(self):
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer'
        self._git('worktree', 'add', '-b', 'review/branch', str(reviewer_root), result_sha)
        self._write_registration('reviewer-task', 'review-agent', 'Reviewer session',
            reviewer_root, 'review/branch', result_sha, result_sha,
            native_session_id='native-reviewer', github_login='reviewer-user')
        pr = {'state': 'open', 'merged': False, 'head': {'sha': result_sha},
              'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        reviews = [{'state': 'APPROVED', 'commit_id': result_sha,
                    'user': {'login': 'reviewer-user'},
                    'submitted_at': '2026-10-21T10:00:00Z',
                    'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' +
                            self._review_binding(head=result_sha)}]
        for status, conclusion in (('completed', 'failure'), ('queued', None)):
            checks = {'total_count': 2, 'check_runs': [
                {'id': 1, 'name': 'check (macOS 3.13)', 'head_sha': result_sha,
                 'status': 'completed', 'conclusion': 'success'},
                {'id': 3, 'name': 'check (macOS 3.13)', 'head_sha': result_sha,
                 'status': status, 'conclusion': conclusion}]}
            with self.subTest(newer_status=status), \
                 patch('llmtool.task_completion._github_get', side_effect=self._github_response_sequence(
                       result_sha, pr, reviews, checks)):
                result = task_completion.collect_github_evidence(
                    **self._github_kwargs(result_sha))
            self.assertIn('REQUIRED_CI_MISSING', result['missing_gates'])

    def test_ci_requires_authoritative_workflow_and_github_actions_publisher(self):
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer'
        self._git('worktree', 'add', '-b', 'review/branch', str(reviewer_root), result_sha)
        self._write_registration('reviewer-task', 'review-agent', 'Reviewer session',
            reviewer_root, 'review/branch', result_sha, result_sha,
            native_session_id='native-reviewer', github_login='reviewer-user')
        pr = {'state': 'open', 'merged': False, 'head': {'sha': result_sha},
              'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        reviews = [{'state': 'APPROVED', 'commit_id': result_sha,
                    'user': {'login': 'reviewer-user'},
                    'submitted_at': '2026-10-21T10:00:00Z',
                    'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' +
                            self._review_binding(head=result_sha)}]
        checks = {'total_count': 1, 'check_runs': [
            {'id': i, 'name': name, 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success'}
            for i, name in enumerate(('check (macOS 3.13)',), 1)]}
        wrong_workflow = {'id': 42, 'path': '.github/workflows/other.yml',
                          'name': 'llmtool Mac CI', 'state': 'active'}
        with patch('llmtool.task_completion._github_get', side_effect=self._github_response_sequence(
                result_sha, pr, reviews, checks, workflow=wrong_workflow)):
            with self.assertRaises(LLMToolError) as rejected:
                task_completion.collect_github_evidence(**self._github_kwargs(result_sha))
        self.assertEqual(rejected.exception.completion_code, 'CI_WORKFLOW_MISMATCH')

        for check in checks['check_runs']:
            check['app'] = {'id': 999, 'slug': 'unrelated-app', 'name': 'Unrelated App'}
        with patch('llmtool.task_completion._github_get', side_effect=self._github_response_sequence(
                result_sha, pr, reviews, checks)):
            evidence = task_completion.collect_github_evidence(**self._github_kwargs(result_sha))
        self.assertIn('REQUIRED_CI_MISSING', evidence['missing_gates'])
        self.assertEqual(evidence['evidence']['successful_checks'], ['check (Linux 3.14)'])

    def test_ci_rejects_wrong_runner_and_legacy_linux_check(self):
        result_sha = 'a' * 40
        pr_number = 10
        def keyed_responses(api):
            return {
                'actions/workflows/llmtool-mac-ci.yml': api[3],
                f"actions/workflows/42/runs?head_sha={result_sha}&event=pull_request&per_page=100": api[4],
                'actions/runs/100/attempts/1/jobs?per_page=100': api[5],
                'commits/' + result_sha + '/check-runs?per_page=100': api[10],
                'actions/workflows/llmtool-linux-ci.yml': api[7],
                f"actions/workflows/43/runs?head_sha={result_sha}&event=pull_request&per_page=100": api[8],
                'actions/runs/200/attempts/1/jobs?per_page=100': api[9],
            }

        checks = {'total_count': 1, 'check_runs': [
            {'id': 1, 'name': 'check (macOS 3.13)', 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success'}]}
        for linux_case in ('unavailable', 'malformed', 'successful', 'pending'):
            api = self._github_response_sequence(result_sha, {}, [], checks)
            api[5]['jobs'][0]['runner_name'] = 'GitHub Actions 1000003657'
            responses = keyed_responses(api)
            if linux_case == 'malformed':
                responses['actions/workflows/llmtool-linux-ci.yml']['path'] = (
                    '.github/workflows/wrong.yml')
            elif linux_case == 'pending':
                api[8]['workflow_runs'][0].update(status='in_progress', conclusion=None)
                api[9]['jobs'][0].update(status='in_progress', conclusion=None)
                api[10]['check_runs'][0].update(status='in_progress', conclusion=None)
            def get_response(path):
                if (linux_case == 'unavailable' and
                        path == 'actions/workflows/llmtool-linux-ci.yml'):
                    task_completion._stop('GITHUB_EVIDENCE_UNAVAILABLE')
                return responses[path]
            with self.subTest(linux_evidence=linux_case), \
                    patch('llmtool.task_completion._github_get', side_effect=get_response):
                with self.assertRaises(LLMToolError) as rejected:
                    task_completion._collect_ci_evidence(
                        pr_number=pr_number, result_sha=result_sha)
            self.assertEqual(rejected.exception.completion_code, 'CI_WORKFLOW_JOBS_INVALID')
        for name in ('davidproject-self-action-3', 'davidproject-self-action-2 ', ''):
            api = self._github_response_sequence(result_sha, {}, [], checks)
            api[5]['jobs'][0]['runner_name'] = name
            with self.subTest(runner=name), \
                    patch('llmtool.task_completion._github_get',
                          side_effect=keyed_responses(api).__getitem__):
                with self.assertRaises(LLMToolError):
                    task_completion._collect_ci_evidence(pr_number=pr_number, result_sha=result_sha)
        api = self._github_response_sequence(result_sha, {}, [], checks)
        api[5]['jobs'][0]['runner_name'] = 'davidproject-self-action-2'
        with patch('llmtool.task_completion._github_get', side_effect=api[3:]):
            second = task_completion._collect_ci_evidence(pr_number=pr_number, result_sha=result_sha)
        self.assertEqual(second['successful_checks'], [
            'check (Linux 3.14)', 'check (macOS 3.13)'])

        legacy_checks = {'total_count': 1, 'check_runs': [
            {'id': 2, 'name': 'check (3.9)', 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success'}]}
        api = self._github_response_sequence(result_sha, {}, [], legacy_checks)
        api[5]['jobs'][0]['name'] = 'check (3.9)'
        api[5]['jobs'][0]['check_run_url'] = (
            'https://api.github.com/repos/davidcatcat/davidcattoolsproject/check-runs/2')
        with patch('llmtool.task_completion._github_get', side_effect=api[3:6] + api[7:]):
            evidence = task_completion._collect_ci_evidence(
                pr_number=pr_number, result_sha=result_sha)
        self.assertEqual(evidence['successful_checks'], ['check (Linux 3.14)'])

        api = self._github_response_sequence(result_sha, {}, [], checks)
        api[9]['jobs'][0]['name'] = 'check (Linux 3.9)'
        api[9]['jobs'][0]['check_run_url'] = (
            'https://api.github.com/repos/davidcatcat/davidcattoolsproject/check-runs/2')
        api[10] = {'total_count': 1, 'check_runs': [
            {'id': 2, 'name': 'check (Linux 3.9)', 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success',
             'app': {'id': 15368, 'slug': 'github-actions', 'name': 'GitHub Actions'}}]}
        with patch('llmtool.task_completion._github_get', side_effect=api[3:10]):
            evidence = task_completion._collect_ci_evidence(
                pr_number=pr_number, result_sha=result_sha)
        self.assertEqual(evidence['successful_checks'], ['check (macOS 3.13)'])

    def test_ci_selects_latest_exact_pull_request_workflow_run_and_attempt(self):
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer'
        self._git('worktree', 'add', '-b', 'review/branch', str(reviewer_root), result_sha)
        self._write_registration('reviewer-task', 'claude-reviewer', 'Reviewer session',
            reviewer_root, 'review/branch', result_sha, result_sha,
            native_session_id='native-reviewer', github_login='reviewer-user')
        pr = {'state': 'open', 'merged': False, 'head': {'sha': result_sha},
              'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        reviews = [{'state': 'APPROVED', 'commit_id': result_sha,
                    'user': {'login': 'reviewer-user'},
                    'submitted_at': '2026-10-21T10:00:00Z',
                    'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' +
                            self._review_binding(head=result_sha)}]
        runs = {'total_count': 2, 'workflow_runs': [
            {'id': 100, 'run_number': 4, 'run_attempt': 1, 'workflow_id': 42,
             'path': '.github/workflows/llmtool-mac-ci.yml', 'event': 'pull_request',
             'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success',
             'pull_requests': [{'number': 10, 'head': {'sha': result_sha}}]},
            {'id': 101, 'run_number': 5, 'run_attempt': 2, 'workflow_id': 42,
             'path': '.github/workflows/llmtool-mac-ci.yml', 'event': 'pull_request',
             'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success',
             'pull_requests': [{'number': 10, 'head': {'sha': result_sha}}]}]}
        jobs = {'total_count': 1, 'jobs': [
            {'id': 11 + index, 'run_id': 101, 'name': name, 'runner_name': 'davidproject-self-action',
             'check_run_url': ('https://api.github.com/repos/davidcatcat/davidcattoolsproject/'
                               f'check-runs/{21 + index}'),
             'status': 'completed', 'conclusion': 'success'}
            for index, name in enumerate(('check (macOS 3.13)',))]}
        checks = {'total_count': 1, 'check_runs': [
            {'id': 21 + index, 'name': name, 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success',
             'app': {'id': 15368, 'slug': 'github-actions', 'name': 'GitHub Actions'}}
            for index, name in enumerate(('check (macOS 3.13)',))]}
        api = self._github_response_sequence(result_sha, pr, reviews, checks,
                                             runs=runs, jobs=jobs)
        with patch('llmtool.task_completion._github_get', side_effect=api) as github_get:
            evidence = task_completion.collect_github_evidence(**self._github_kwargs(
                result_sha, task_record={'developer': {'provider_profile': 'codex'}}))
        self.assertEqual(evidence['evidence']['ci_run_id'], 101)
        self.assertEqual(evidence['evidence']['ci_run_attempt'], 2)
        self.assertEqual(evidence['evidence']['ci_check_ids'],
                         {'check (Linux 3.14)': 61, 'check (macOS 3.13)': 21})
        self.assertEqual(evidence['missing_gates'], [])
        self.assertIn('actions/runs/101/attempts/2/jobs?per_page=100',
                      github_get.call_args_list[5].args[0])

    def test_linux_check_is_required_for_code_changes_without_a_linux_run(self):
        result_sha = 'a' * 40
        mac_workflow = {'id': 42, 'path': '.github/workflows/llmtool-mac-ci.yml',
                        'name': 'llmtool Mac CI', 'state': 'active'}
        linux_workflow = {'id': 43, 'path': '.github/workflows/llmtool-linux-ci.yml',
                          'name': 'llmtool Linux CI', 'state': 'active'}
        no_runs = {'total_count': 0, 'workflow_runs': []}
        api = [mac_workflow, no_runs, linux_workflow, no_runs]
        with patch('llmtool.task_completion._github_get', side_effect=api):
            evidence = task_completion._collect_ci_evidence(
                pr_number=10, result_sha=result_sha)
        self.assertTrue(evidence['linux_required'])
        self.assertNotIn('check (Linux 3.14)', evidence['successful_checks'])

    def test_linux_workflow_success_is_collected_on_its_ec2_runner(self):
        result_sha = 'a' * 40
        mac_workflow = {'id': 42, 'path': '.github/workflows/llmtool-mac-ci.yml',
                        'name': 'llmtool Mac CI', 'state': 'active'}
        linux_workflow = {'id': 43, 'path': '.github/workflows/llmtool-linux-ci.yml',
                          'name': 'llmtool Linux CI', 'state': 'active'}
        no_runs = {'total_count': 0, 'workflow_runs': []}
        linux_run = {'total_count': 1, 'workflow_runs': [{
            'id': 200, 'run_number': 8, 'run_attempt': 1, 'workflow_id': 43,
            'path': '.github/workflows/llmtool-linux-ci.yml', 'event': 'pull_request',
            'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success',
            'pull_requests': [{'number': 10, 'head': {'sha': result_sha}}]}]}
        linux_jobs = {'total_count': 1, 'jobs': [{
            'id': 51, 'run_id': 200, 'name': 'check (Linux 3.14)',
            'runner_name': 'ec2-self-runner',
            'check_run_url': ('https://api.github.com/repos/davidcatcat/'
                              'davidcattoolsproject/check-runs/61'),
            'status': 'completed', 'conclusion': 'success'}]}
        linux_checks = {'total_count': 1, 'check_runs': [{
            'id': 61, 'name': 'check (Linux 3.14)', 'head_sha': result_sha,
            'status': 'completed', 'conclusion': 'success',
            'app': {'id': 15368, 'slug': 'github-actions', 'name': 'GitHub Actions'}}]}
        api = [mac_workflow, no_runs, linux_workflow, linux_run, linux_jobs, linux_checks]
        with patch('llmtool.task_completion._github_get', side_effect=api):
            evidence = task_completion._collect_ci_evidence(
                pr_number=10, result_sha=result_sha)
        self.assertTrue(evidence['linux_required'])
        self.assertEqual(evidence['successful_checks'], ['check (Linux 3.14)'])
        self.assertEqual(evidence['check_ids'], {'check (Linux 3.14)': 61})

    def test_docs_only_ci_uses_docs_check_and_fails_closed_when_missing(self):
        result_sha = 'a' * 40
        docs_paths = ['README.md', 'docs/guide.md']
        self.assertTrue(task_completion._docs_only_ci(docs_paths))
        self.assertFalse(task_completion._docs_only_ci([]))
        self.assertFalse(task_completion._docs_only_ci(['docs/guide.md', 'src/code.py']))
        self.assertFalse(task_completion._docs_only_ci(['tools/llmtool/x/y.md']))
        self.assertTrue(task_completion._docs_only_ci(['tools/llmtool/README.md']))
        self.assertFalse(task_completion._docs_only_ci(['tools/llmtool/x.py']))
        self.assertFalse(task_completion._docs_only_ci(None))

        checks = {'total_count': 1, 'check_runs': [{
            'id': 61, 'name': 'check (docs)', 'head_sha': result_sha,
            'status': 'completed', 'conclusion': 'success',
            'app': {'id': 15368, 'slug': 'github-actions', 'name': 'GitHub Actions'}}]}
        api = self._github_response_sequence(result_sha, {}, [], checks)
        api[9]['jobs'][0]['name'] = 'check (docs)'
        api[9]['jobs'][0]['check_run_url'] = (
            'https://api.github.com/repos/davidcatcat/davidcattoolsproject/check-runs/61')
        api[10] = checks
        with patch('llmtool.task_completion._github_get', side_effect=api[7:]) as github_get:
            evidence = task_completion._collect_ci_evidence(
                pr_number=10, result_sha=result_sha, changed_paths=docs_paths)
        self.assertEqual(github_get.call_count, 4)
        self.assertEqual(evidence['required_checks'], ['check (docs)'])
        self.assertEqual(evidence['successful_checks'], ['check (docs)'])
        self.assertTrue(evidence['docs_only'])

        missing_api = self._github_response_sequence(result_sha, {}, [], {
            'total_count': 0, 'check_runs': []})
        with patch('llmtool.task_completion._github_get',
                   side_effect=missing_api[7:]) as github_get:
            missing = task_completion._collect_ci_evidence(
                pr_number=10, result_sha=result_sha, changed_paths=docs_paths)
        self.assertEqual(github_get.call_count, 3)
        self.assertEqual(missing['required_checks'], ['check (docs)'])
        self.assertEqual(missing['successful_checks'], [])

    def test_mixed_or_empty_paths_keep_full_code_ci_requirements(self):
        result_sha = 'a' * 40
        for changed_paths in (['docs/guide.md', 'src/code.py'], []):
            with self.subTest(changed_paths=changed_paths):
                self.assertFalse(task_completion._docs_only_ci(changed_paths))
                api = self._github_response_sequence(result_sha, {}, [], {
                    'total_count': 0, 'check_runs': []})
                with patch('llmtool.task_completion._github_get',
                           side_effect=api[3:]) as github_get:
                    evidence = task_completion._collect_ci_evidence(
                        pr_number=10, result_sha=result_sha,
                        changed_paths=changed_paths)
                self.assertEqual(github_get.call_count, 8)
                self.assertEqual(evidence['required_checks'], [
                    'check (Linux 3.14)', 'check (macOS 3.13)'])
                self.assertFalse(evidence['docs_only'])

    def test_newer_failed_workflow_run_defeats_older_success(self):
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer'
        self._git('worktree', 'add', '-b', 'review/branch', str(reviewer_root), result_sha)
        self._write_registration('reviewer-task', 'review-agent', 'Reviewer session',
            reviewer_root, 'review/branch', result_sha, result_sha,
            native_session_id='native-reviewer', github_login='reviewer-user')
        pr = {'state': 'open', 'merged': False, 'head': {'sha': result_sha},
              'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        reviews = [{'state': 'APPROVED', 'commit_id': result_sha,
                    'user': {'login': 'reviewer-user'},
                    'submitted_at': '2026-10-21T10:00:00Z',
                    'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' +
                            self._review_binding(head=result_sha)}]
        runs = {'total_count': 2, 'workflow_runs': [
            {'id': 100, 'run_number': 4, 'run_attempt': 1, 'workflow_id': 42,
             'path': '.github/workflows/llmtool-mac-ci.yml', 'event': 'pull_request',
             'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success',
             'pull_requests': [{'number': 10, 'head': {'sha': result_sha}}]},
            {'id': 101, 'run_number': 5, 'run_attempt': 1, 'workflow_id': 42,
             'path': '.github/workflows/llmtool-mac-ci.yml', 'event': 'pull_request',
             'head_sha': result_sha, 'status': 'completed', 'conclusion': 'failure',
             'pull_requests': [{'number': 10, 'head': {'sha': result_sha}}]}]}
        jobs = {'total_count': 1, 'jobs': [
            {'id': 11 + index, 'run_id': 101, 'name': name, 'runner_name': 'davidproject-self-action',
             'check_run_url': ('https://api.github.com/repos/davidcatcat/davidcattoolsproject/'
                               f'check-runs/{21 + index}'),
             'status': 'completed', 'conclusion': 'failure'}
            for index, name in enumerate(('check (macOS 3.13)',))]}
        checks = {'total_count': 1, 'check_runs': [
            {'id': 21 + index, 'name': name, 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'failure',
             'app': {'id': 15368, 'slug': 'github-actions', 'name': 'GitHub Actions'}}
            for index, name in enumerate(('check (macOS 3.13)',))]}
        api = self._github_response_sequence(result_sha, pr, reviews, checks,
                                             runs=runs, jobs=jobs)
        with patch('llmtool.task_completion._github_get', side_effect=api):
            evidence = task_completion.collect_github_evidence(**self._github_kwargs(result_sha))
        self.assertEqual(evidence['evidence']['ci_run_id'], 101)
        self.assertIn('REQUIRED_CI_MISSING', evidence['missing_gates'])
        self.assertEqual(evidence['evidence']['successful_checks'], ['check (Linux 3.14)'])

    def _task(self, state='WAITING_EXECUTOR', missing=None):
        task_id, run_id = new_id(), new_id()
        value = _record(task_id, run_id, 'pm-' + new_id(), 'original-pm',
                        'Add a bounded test.', state='PM_RESPONDED',
                        provider_type='openai-compatible', model='pm-model',
                        backend='NO_NATIVE_SESSION_ID_EXPOSED',
                        request_sha=hashlib.sha256(b'Add a bounded test.').hexdigest())
        value['pm_route_binding'] = {
            'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None}
        value['response'] = 'Original PM selected the bounded company route.'
        value['pm_assignment'] = {'schema_version': 1, 'route': 'company-gw', 'risk': 'LOW',
                                  'goal': 'Add bounded test.', 'acceptance': ['test passes']}
        value['developer'] = {
            'assignment_id': new_id(), 'selection_source': 'PM', 'state': 'DEVELOPER_RESPONDED',
            'provider_profile': 'company-gw', 'provider_type': 'openai-compatible',
            'model': 'gw-model', 'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
            'session_id': 'dev-' + new_id(), 'source_sha': self.source_sha,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': [self.source_path], 'write_paths': [self.source_path],
            'acceptance': ['test passes'], 'context_sha256': 'a' * 64,
            'egress_basis': 'Non-sensitive source context approved', 'input_bytes': 10,
            'max_tokens': 64, 'artifact_format': 'unified_diff', 'artifact': 'proposal',
            'artifact_sha256': hashlib.sha256(b'proposal').hexdigest(),
            'usage': None, 'error_code': None, 'created_at': value['created_at']}
        value['completion'] = {'state': state, 'executor_task_id': self.task_id,
            'reviewer_task_id': None, 'evidence': {'task_id': task_id, 'run_id': run_id,
                'contract_digest': value['task_binding']['contract_digest'],
                'executor_identity': 'executor-agent', 'executor_session_ref': 'Executor D6 session',
                'executor_native_session_id': None, 'executor_native_session': 'UNKNOWN',
                'root': str(self.root), 'common_dir': self.common, 'branch': 'feat/executor',
                'source_sha': self.source_sha, 'result_head': self.source_sha},
            'missing_gates': sorted(missing or []), 'pm_dispatch': 'NOT_STARTED',
            'pm_response': None, 'pm_recommendation': None, 'pm_usage': None,
            'pm_error_code': None}
        return value

    def test_completion_missing_gate_waits_for_reviewer_without_calling_pm(self):
        store = TaskStore(self.base / 'tasks')
        task = self._task()
        store.create(task)
        out = StringIO()
        args = Namespace(task_id=task['task_id'], executor_task=self.task_id,
                         reviewer_task='reviewer-task', pr=10)
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_result',
                   return_value={'task_id': task['task_id'], 'run_id': task['run_id'],
                       'executor_task_id': self.task_id, 'executor_identity': 'executor-agent',
                       'executor_session_ref': 'Executor D6 session', 'root': str(self.root),
                       'common_dir': self.common, 'branch': 'feat/executor',
                       'source_sha': self.source_sha, 'result_head': '4' * 40,
                       'changed_paths': [self.source_path], 'diff_sha256': '5' * 64}), \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_tests',
                   return_value=[{'path': self.source_path, 'source_sha': '4' * 40,
                                  'exit_code': 0, 'output_sha256': '6' * 64}]), \
             patch('llmtool.task_submission_cli.task_completion.collect_github_evidence',
                   return_value={'evidence': {'pr_number': 10, 'head_sha': '4' * 40,
                        'formal_review_state': 'UNVERIFIED', 'required_checks': [],
                        'successful_checks': [], 'ci_head_sha': '4' * 40},
                        'missing_gates': ['REVIEWER_D6_SESSION_NOT_INDEPENDENT']}), \
             patch('llmtool.task_submission_cli.resolve_profile_config') as resolve, \
             redirect_stdout(out):
            self.assertEqual(run_task_complete(args, store=store), 1)
        saved = store.get(task['task_id'])
        self.assertEqual(saved['completion']['state'], 'WAITING_REVIEW')
        self.assertEqual(saved['completion']['pm_dispatch'], 'NOT_STARTED')
        resolve.assert_not_called()

    def test_executor_event_returns_bound_result_to_original_pm_before_review(self):
        from llmtool.task_submission_cli import run_task_executor_event

        store = TaskStore(self.base / 'executor-event-tasks')
        task = self._task()
        store.create(task)
        result_sha = '8' * 40
        git = {'task_id': task['task_id'], 'run_id': task['run_id'],
            'executor_task_id': self.task_id, 'executor_identity': 'executor-agent',
            'executor_session_ref': 'Executor D6 session', 'root': str(self.root),
            'common_dir': self.common, 'branch': 'feat/executor',
            'source_sha': self.source_sha, 'result_head': result_sha,
            'changed_paths': [self.source_path], 'diff_sha256': '5' * 64}
        tests = [{'path': self.source_path, 'source_sha': result_sha,
                  'exit_code': 0, 'output_sha256': '6' * 64}]
        client = FakeClient(Reply(json.dumps({'schema_version': 1,
            'decision': 'SEND_TO_REVIEW', 'summary': 'Executor result is ready for independent review.'}),
            {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}))
        out = StringIO()
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_result',
                   return_value=git), \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_tests',
                   return_value=tests), \
             patch('llmtool.task_submission_cli.task_completion.collect_github_evidence') as github, \
             patch('llmtool.task_submission_cli.resolve_profile_config', return_value=pm_config('original-pm')) as resolve, \
             patch('llmtool.task_submission_cli.api_key', return_value='key-not-recorded'), \
             patch('llmtool.task_submission_cli.selected_provider', return_value={
                   'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None}), \
             patch('llmtool.task_submission_cli.create_client', return_value=client), \
             redirect_stdout(out):
            self.assertEqual(run_task_executor_event(task['task_id'], self.task_id,
                             store=store), 0)
        saved = store.get(task['task_id'])
        completion = saved['completion']
        self.assertEqual(completion['state'], 'PM_RETURNED')
        self.assertEqual(completion['pm_recommendation']['decision'], 'SEND_TO_REVIEW')
        self.assertEqual(completion['evidence']['result']['result_head'], result_sha)
        self.assertEqual(completion['evidence']['tests'], tests)
        self.assertEqual(completion['evidence']['review_pr_ci'], {
            'stage': 'EXECUTOR_RESULT', 'formal_review_state': 'NOT_STARTED',
            'ci_state': 'NOT_STARTED'})
        self.assertIsNone(completion['reviewer_task_id'])
        resolve.assert_called_once_with('original-pm', require_configured=True)
        github.assert_not_called()
        packet = json.loads(client.calls[0][1]['content'])
        self.assertEqual(packet['task_id'], task['task_id'])
        self.assertEqual(packet['run_id'], task['run_id'])
        self.assertEqual(packet['result_head'], result_sha)
        self.assertNotIn('artifact', packet)
        with self.assertRaises(LLMToolError):
            run_task_executor_event(task['task_id'], self.task_id, store=store)
        self.assertEqual(len(client.calls), 1)

    def test_direct_opencode_result_event_returns_to_original_pm_once(self):
        from llmtool.task_submission_cli import (
            run_task_direct_result_event)
        task, store, result, tests = self._direct_opencode_task('direct-result-tasks')
        diff_sha = result['diff_sha256']
        client = FakeClient(Reply(json.dumps({'schema_version': 1,
            'decision': 'SEND_TO_REVIEW',
            'summary': 'Verified uncommitted result is ready for review.'}),
            {'prompt_tokens': 10, 'completion_tokens': 8, 'total_tokens': 18}))
        output = StringIO()
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result',
                   return_value=result) as collect, \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_worktree_tests',
                   return_value=tests) as run_tests, \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   return_value=pm_config('original-pm')) as resolve, \
             patch('llmtool.task_submission_cli.api_key', return_value='not-persisted'), \
             patch('llmtool.task_submission_cli.selected_provider', return_value={
                   'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None}), \
             patch('llmtool.task_submission_cli.create_client', return_value=client), \
             redirect_stdout(output):
            self.assertEqual(run_task_direct_result_event(
                task['task_id'], self.task_id, store=store), 1, output.getvalue())

        saved = store.get(task['task_id'])
        callback = saved['developer']['direct_result_callback']
        self.assertEqual(callback['state'], 'PM_RETURNED')
        self.assertEqual(callback['pm_recommendation']['decision'], 'SEND_TO_REVIEW')
        handoff = callback['review_handoff']
        self.assertEqual(handoff['receipt']['verdict'], 'BLOCKED')
        self.assertEqual(handoff['receipt']['execution_status'], 'NOT_DISPATCHED')
        self.assertEqual(handoff['next_step'], 'WAIT_FOR_REVIEWER')
        self.assertIsNone(handoff['receipt']['reviewer_identity'])
        self.assertEqual(callback['evidence']['result']['result_head'], None)
        self.assertEqual(callback['evidence']['review_pr_ci'], {
            'stage': 'EXECUTOR_RESULT', 'formal_review_state': 'NOT_STARTED',
            'ci_state': 'NOT_STARTED'})
        self.assertIsNone(saved['completion'])
        self.assertEqual((collect.call_count, run_tests.call_count, len(client.calls)), (3, 1, 1))
        packet = json.loads(client.calls[0][1]['content'])
        self.assertIsNone(packet['result_head'])
        self.assertEqual(packet['review_pr_ci']['formal_review_state'], 'NOT_STARTED')
        # Even if a later committed completion record exists, the public
        # executor-only route must not issue a second PM request for this Task.
        committed = {'state': 'WAITING_EXECUTOR', 'executor_task_id': self.task_id,
            'reviewer_task_id': None, 'evidence': {
                'task_id': task['task_id'], 'run_id': task['run_id'],
                'contract_digest': task['task_binding']['contract_digest'],
                'executor_identity': result['executor_identity'],
                'executor_session_ref': result['executor_session_ref'],
                'executor_native_session_id': result['executor_native_session_id'],
                'executor_native_session': 'VERIFIED', 'root': result['root'],
                'common_dir': result['common_dir'], 'branch': result['branch'],
                'source_sha': result['source_sha'], 'result_head': self.source_sha},
            'missing_gates': [], 'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
            'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None}
        store.update(task['task_id'], completion=committed)
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_result') as collect_committed, \
             patch('llmtool.task_submission_cli.resolve_profile_config') as resolve_committed, \
             redirect_stdout(StringIO()):
            self.assertEqual(run_task_complete(Namespace(task_id=task['task_id'],
                executor_task=self.task_id, reviewer_task=None, pr=None), store=store), 1)
        collect_committed.assert_not_called()
        resolve_committed.assert_not_called()
        self.assertEqual(len(client.calls), 1)
        current = store.get(task['task_id'])
        unknown_callback = {**current['developer']['direct_result_callback'],
            'state': 'PM_OUTCOME_UNKNOWN', 'pm_dispatch': 'UNKNOWN',
            'pm_response': None, 'pm_recommendation': None,
            'pm_usage': None, 'pm_error_code': 'PM_CALL_INTERRUPTED'}
        unknown_callback.pop('review_handoff', None)
        store.update(task['task_id'], developer={**current['developer'],
            'direct_result_callback': unknown_callback})
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_result') as collect_unknown, \
             patch('llmtool.task_submission_cli.resolve_profile_config') as resolve_unknown, \
             self.assertRaises(LLMToolError):
            run_task_complete(Namespace(task_id=task['task_id'],
                executor_task=self.task_id, reviewer_task=None, pr=None), store=store)
        collect_unknown.assert_not_called()
        resolve_unknown.assert_not_called()
        self.assertEqual(len(client.calls), 1)
        with self.assertRaises(LLMToolError):
            run_task_direct_result_event(task['task_id'], self.task_id, store=store)
        self.assertEqual(len(client.calls), 1)
        resolve.assert_called_once_with('original-pm', require_configured=True)

    def _active_pm_task(self, template, route='opencode'):
        """Overlay a fixture's Developer/completion onto a real record-pm Task."""
        from llmtool.task_submission_cli import run_task_record_pm

        response = json.dumps({'schema_version': 1, 'summary': 'Active PM plan.',
            'developer_assignment': {'route': route, 'risk': 'LOW',
                'goal': 'Add bounded test.', 'acceptance': ['test passes']}})
        scratch = TaskStore(self.base / ('record-pm-' + new_id()))
        envelope = StringIO(json.dumps({'request': 'Add a bounded test.',
                                        'response': response}))
        with redirect_stdout(StringIO()) as out:
            run_task_record_pm(Namespace(stdin_json=True), store=scratch,
                               input_stream=envelope)
        record = scratch.get(json.loads(out.getvalue())['task_id'])
        record.pop('record_sha256', None)
        developer = dict(template['developer'])
        completion = template['completion']
        if completion is not None:
            completion = json.loads(json.dumps(completion))
            completion['evidence'].update(task_id=record['task_id'], run_id=record['run_id'],
                contract_digest=record['task_binding']['contract_digest'])
        record.update(developer=developer, completion=completion)
        return record

    def test_active_pm_runtime_completion_persists_evidence_without_provider(self):
        from llmtool.task_submission_cli import (_COMPLETION_SYSTEM,
            _EXECUTOR_RESULT_SYSTEM, _completion_packet, run_task_show)

        task = self._active_pm_task(self._task())
        store = TaskStore(self.base / 'active-pm-tasks')
        store.create(task)
        git, tests, gh = self._mock_complete_evidence(task)
        forbidden = AssertionError('an active PM runtime has no provider profile')

        def complete(**namespace):
            out = StringIO()
            with patch('llmtool.task_submission_cli.task_completion.collect_executor_result',
                       return_value=git), \
                 patch('llmtool.task_submission_cli.task_completion.run_bounded_tests',
                       return_value=tests), \
                 patch('llmtool.task_submission_cli.task_completion.collect_github_evidence',
                       return_value=gh), \
                 patch('llmtool.task_submission_cli.resolve_profile_config',
                       side_effect=forbidden), \
                 patch('llmtool.task_submission_cli.api_key', side_effect=forbidden), \
                 patch('llmtool.task_submission_cli.create_client', side_effect=forbidden), \
                 redirect_stdout(out):
                code = run_task_complete(Namespace(task_id=task['task_id'],
                    executor_task=self.task_id, **namespace), store=store)
            return code, json.loads(out.getvalue())

        code, view = complete(reviewer_task=None, pr=None)
        self.assertEqual(code, 0)
        completion = view['completion']
        self.assertEqual(completion['state'], 'PM_EVIDENCE_READY')
        self.assertEqual(completion['pm_dispatch'], 'NOT_STARTED')
        self.assertEqual(completion['missing_gates'], [])
        self.assertIsNone(completion['pm_error_code'])
        self.assertIsNone(completion['pm_response'])
        self.assertEqual(completion['evidence']['result'], git)
        self.assertEqual(completion['evidence']['review_pr_ci']['stage'], 'EXECUTOR_RESULT')
        handoff = view['active_pm_handoff']
        self.assertEqual(handoff['provider_invoked'], False)
        self.assertEqual(handoff['instructions'], _EXECUTOR_RESULT_SYSTEM)
        saved = store.get(task['task_id'])
        self.assertEqual(handoff['packet'],
                         _completion_packet(saved, saved['completion']['evidence']))
        self.assertNotIn('artifact', json.dumps(handoff['packet']))
        shown = StringIO()
        run_task_show(Namespace(task_id=task['task_id']), store=store, stdout=shown)
        self.assertEqual(json.loads(shown.getvalue())['active_pm_handoff'], handoff)

        # The same evidence gates still run on the post-review/CI path; the result
        # again stays with the active PM instead of any provider profile.
        code, view = complete(reviewer_task='reviewer-task', pr=10)
        self.assertEqual(code, 0)
        self.assertEqual(view['completion']['state'], 'PM_EVIDENCE_READY')
        self.assertEqual(view['completion']['evidence']['review_pr_ci']['formal_review_state'],
                         'ACCEPTED')
        self.assertEqual(view['active_pm_handoff']['instructions'], _COMPLETION_SYSTEM)

    def test_active_pm_runtime_completion_keeps_missing_gates_waiting(self):
        task = self._active_pm_task(self._task())
        store = TaskStore(self.base / 'active-pm-gate-tasks')
        store.create(task)
        git, tests, _ = self._mock_complete_evidence(task)
        failing = [dict(tests[0], exit_code=1)]
        out = StringIO()
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_result',
                   return_value=git), \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_tests',
                   return_value=failing), \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   side_effect=AssertionError('no PM call')), \
             redirect_stdout(out):
            code = run_task_complete(Namespace(task_id=task['task_id'],
                executor_task=self.task_id, reviewer_task=None, pr=None), store=store)
        self.assertEqual(code, 1)
        view = json.loads(out.getvalue())
        self.assertEqual(view['completion']['state'], 'WAITING_EXECUTOR')
        self.assertEqual(view['completion']['missing_gates'], ['TEST_FAILED'])
        self.assertNotIn('active_pm_handoff', view)

    def test_evidence_ready_state_is_rejected_for_provider_pm_tasks(self):
        def ready(task):
            git, tests, _ = self._mock_complete_evidence(task)
            task['completion'] = {**task['completion'], 'state': 'PM_EVIDENCE_READY',
                'evidence': {**task['completion']['evidence'], 'result': git, 'tests': tests,
                    'review_pr_ci': {'stage': 'EXECUTOR_RESULT',
                        'formal_review_state': 'NOT_STARTED', 'ci_state': 'NOT_STARTED'}}}
            return task

        with self.assertRaises(LLMToolError):
            TaskStore(self.base / 'provider-pm-ready-tasks').create(ready(self._task()))
        active = ready(self._active_pm_task(self._task()))
        store = TaskStore(self.base / 'active-ready-tasks')
        store.create(active)
        # Nothing about a PM call may be recorded in the no-provider state.
        for field, value in (('pm_error_code', 'PM_PROVIDER_UNAVAILABLE'),
                             ('missing_gates', ['TEST_FAILED']),
                             ('pm_response', '{}')):
            with self.subTest(field=field), self.assertRaises(LLMToolError):
                store.update(active['task_id'],
                             completion={**active['completion'], field: value})

    def test_active_pm_runtime_direct_result_persists_evidence_without_provider(self):
        from llmtool.task_submission_cli import (_DIRECT_RESULT_SYSTEM,
            _direct_result_packet, run_task_direct_result_event)

        template, _, result, tests = self._direct_opencode_task('direct-template-tasks')
        task = self._active_pm_task(template)
        callback = task['developer']['direct_result_callback']
        task['developer']['direct_result_callback'] = {**callback, 'evidence': {
            **callback['evidence'], 'task_id': task['task_id'], 'run_id': task['run_id'],
            'contract_digest': task['task_binding']['contract_digest']}}
        store = TaskStore(self.base / 'active-direct-tasks')
        store.create(task)
        result = {**result, 'task_id': task['task_id'], 'run_id': task['run_id']}
        forbidden = AssertionError('an active PM runtime has no provider profile')

        def run(entry):
            out = StringIO()
            with patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result',
                       return_value=result), \
                 patch('llmtool.task_submission_cli.task_completion.run_bounded_worktree_tests',
                       return_value=tests) as run_tests, \
                 patch('llmtool.task_submission_cli.resolve_profile_config',
                       side_effect=forbidden), \
                 patch('llmtool.task_submission_cli.create_client', side_effect=forbidden), \
                 redirect_stdout(out):
                code = entry()
            return code, json.loads(out.getvalue()), run_tests.call_count

        code, view, test_runs = run(lambda: run_task_direct_result_event(
            task['task_id'], self.task_id, store=store))
        self.assertEqual((code, test_runs), (0, 1))
        callback = view['developer']['direct_result_callback']
        self.assertEqual(callback['state'], 'PM_EVIDENCE_READY')
        self.assertEqual(callback['pm_dispatch'], 'NOT_STARTED')
        self.assertEqual(callback['evidence']['verification_state'], 'READY')
        handoff = view['active_pm_handoff']
        self.assertEqual(handoff['instructions'], _DIRECT_RESULT_SYSTEM)
        saved = store.get(task['task_id'])
        self.assertEqual(handoff['packet'], _direct_result_packet(
            saved, saved['developer'], self.task_id,
            saved['developer']['direct_result_callback']['evidence']))
        # The public `task complete` retry revalidates the same worktree, never
        # reruns tests and still never resolves a provider profile.
        code, again, test_runs = run(lambda: run_task_complete(Namespace(
            task_id=task['task_id'], executor_task=self.task_id,
            reviewer_task=None, pr=None), store=store))
        self.assertEqual((code, test_runs), (0, 0))
        self.assertEqual(again['active_pm_handoff'], handoff)

    def _direct_opencode_task(self, store_name):
        from llmtool.task_submission_cli import _direct_result_callback

        store = TaskStore(self.base / store_name)
        task = self._task()
        developer = task['developer']
        artifact = 'diff --git a/test_fixture.py b/test_fixture.py\n'
        developer.update(provider_profile='opencode', provider_type='opencode-cli',
            model=opencode_direct_developer.MODEL,
            backend_session_semantics='CLI_REPORTED_SESSION_ID',
            state='DEVELOPER_RESPONDED', artifact=artifact,
            artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest(),
            execution={'status': 'SUCCEEDED', 'native_session_id': 'ses_Developer123',
                'process_exit_code': 0, 'stdout_sha256': 'a' * 64,
                'elapsed_ms': 12, 'cleanup_status': 'CONFIRMED',
                'usage_source': 'UNKNOWN', 'source_bytes': 20,
                'source_file_count': 1, 'source_manifest_sha256': 'b' * 64})
        registration = task_completion.read_d6_registration(
            self.task_id, self.common, allowed_states=task_completion._D6_EVIDENCE_STATES)
        developer['direct_result_callback'] = _direct_result_callback(
            task, registration, self.source_sha)
        task['completion'] = None
        store.create(task)
        diff_sha = 'c' * 64
        result = {'task_id': task['task_id'], 'run_id': task['run_id'],
            'executor_task_id': self.task_id, 'executor_identity': registration.trusted_agent_id,
            'executor_session_ref': registration.session_ref,
            'executor_native_session_id': registration.native_session_id,
            'root': registration.root, 'common_dir': registration.common_dir,
            'branch': registration.branch, 'source_sha': self.source_sha,
            'result_head': None, 'result_mode': 'UNCOMMITTED_WORKTREE',
            'changed_paths': [self.source_path], 'diff_sha256': diff_sha,
            'status_sha256': 'd' * 64, 'file_manifest_sha256': 'e' * 64}
        tests = [{'path': self.source_path, 'source_sha': self.source_sha,
            'result_head': None, 'diff_sha256': diff_sha, 'exit_code': 0,
            'output_sha256': 'f' * 64}]
        return task, store, result, tests

    def _run_direct_pm_send_to_review(self, task, store, result, tests, adapter=None,
                                     reviewer_decision='STOP', client=None,
                                     inject_adapter=False):
        from llmtool.task_submission_cli import run_task_direct_result_event

        if client is None:
            client = FakeClient(replies=[
                Reply(json.dumps({'schema_version': 1,
                    'decision': 'SEND_TO_REVIEW', 'summary': 'Verified result is ready.'}),
                    {'prompt_tokens': 10, 'completion_tokens': 8, 'total_tokens': 18}),
                Reply(json.dumps({'schema_version': 1,
                    'decision': reviewer_decision,
                    'summary': 'Stop after receipt delivery.'}),
                    {'prompt_tokens': 8, 'completion_tokens': 4, 'total_tokens': 12}),
            ])
        output = StringIO()
        with patch('llmtool.task_submission_cli._trusted_reviewer_adapter',
                   None if inject_adapter else adapter), \
             patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result',
                   return_value=result), \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_worktree_tests',
                   return_value=tests), \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   return_value=pm_config('original-pm')) as resolve, \
             patch('llmtool.task_submission_cli.api_key', return_value='not-persisted'), \
             patch('llmtool.task_submission_cli.selected_provider', return_value={
                   'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None}), \
             patch('llmtool.task_submission_cli.create_client', return_value=client), \
             redirect_stdout(output):
            code = run_task_direct_result_event(task['task_id'], self.task_id, store=store,
                reviewer_adapter=adapter if inject_adapter else None)
        return code, client, output.getvalue(), resolve

    def test_host_injected_reviewer_adapter_is_used_by_completion_event(self):
        task, store, result, tests = self._direct_opencode_task('review-host-injection')
        adapter = unittest.mock.Mock(side_effect=lambda intent:
            self._trusted_review_receipt(intent, verdict='PASS'))

        code, client, _, _ = self._run_direct_pm_send_to_review(
            task, store, result, tests, adapter=adapter, inject_adapter=True)

        self.assertEqual(code, 0)
        self.assertEqual(adapter.call_count, 1)
        self.assertEqual(len(client.calls), 2)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'RECEIVED')
        self.assertEqual(callback['review_handoff']['receipt']['verdict'], 'PASS')
        self.assertEqual(callback['review_return']['state'], 'RETURNED')
        self.assertEqual(callback['review_return']['next_step'], 'STOP')
        pm_receipt = json.loads(client.calls[1][1]['content'])
        self.assertEqual(pm_receipt['review_packet_digest'],
                         callback['review_handoff']['receipt']['review_packet_digest'])

    def test_v1_reviewer_adapter_receipt_returns_to_pm_without_packet_digest(self):
        task, store, result, tests = self._direct_opencode_task('review-v1-adapter-receipt')
        adapter = unittest.mock.Mock(side_effect=lambda intent:
            self._legacy_trusted_review_receipt(intent, verdict='PASS'))

        code, client, _, _ = self._run_direct_pm_send_to_review(
            task, store, result, tests, adapter=adapter, inject_adapter=True)

        self.assertEqual(code, 0)
        self.assertEqual(adapter.call_count, 1)
        self.assertEqual(len(client.calls), 2)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        receipt = callback['review_handoff']['receipt']
        self.assertEqual(receipt['schema_version'], 1)
        self.assertNotIn('review_packet_digest', receipt)
        self.assertEqual(callback['review_return']['state'], 'RETURNED')
        pm_receipt = json.loads(client.calls[1][1]['content'])
        self.assertNotIn('review_packet_digest', pm_receipt)
        self.assertEqual(pm_receipt['receipt_sha256'], receipt['receipt_sha256'])

    def test_persisted_v1_received_handoff_recovers_pm_return_without_redispatch(self):
        task, store, result, tests = self._direct_opencode_task('review-v1-received-recovery')
        _, client, _, _ = self._run_direct_pm_send_to_review(
            task, store, result, tests, adapter=None)
        saved = store.get(task['task_id'])
        developer = saved['developer']
        callback = dict(developer['direct_result_callback'])
        intent = callback['review_handoff']['intent']
        receipt = self._legacy_trusted_review_receipt(intent, verdict='PASS')
        handoff = task_completion.review_handoff_record(intent, receipt)
        store.update(task['task_id'], developer={**developer,
            'direct_result_callback': {**callback, 'review_handoff': handoff}})
        adapter = unittest.mock.Mock(side_effect=AssertionError('RECEIVED must not redispatch'))

        code, resumed_client, _, _ = self._run_direct_pm_send_to_review(
            task, store, result, tests, adapter=adapter, client=client,
            inject_adapter=True)

        self.assertEqual(code, 0)
        self.assertEqual(len(resumed_client.calls), 2)
        adapter.assert_not_called()
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'RECEIVED')
        self.assertEqual(callback['review_handoff']['receipt'], receipt)
        self.assertEqual(callback['review_return']['state'], 'RETURNED')
        pm_receipt = json.loads(resumed_client.calls[1][1]['content'])
        self.assertNotIn('review_packet_digest', pm_receipt)
        self.assertEqual(pm_receipt['receipt_sha256'], receipt['receipt_sha256'])

    @staticmethod
    def _trusted_review_receipt(intent, *, verdict='PASS'):
        blocked = verdict == 'BLOCKED'
        value = {'schema_version': 2, 'dispatch_id': intent['dispatch_id'],
            'task_id': intent['task_id'], 'run_id': intent['run_id'],
            'contract_digest': intent['contract_digest'], 'source_sha': intent['source_sha'],
            'result_head': intent['result_head'], 'diff_sha256': intent['diff_sha256'],
            'developer_task_id': intent['developer_task_id'],
            'reviewer_task_id': 'reviewer-task', 'reviewer_identity': 'reviewer-agent',
            'reviewer_session_ref': 'reviewer-session',
            'review_packet_digest': '7' * 64, 'verdict': verdict,
            'security': 'BLOCKED' if blocked else 'PASS',
            'functional': 'BLOCKED' if blocked else 'PASS',
            'execution_status': 'FAILED' if blocked else 'SUCCEEDED',
            'code': 'REVIEW_BLOCKED' if blocked else 'REVIEW_COMPLETED',
            'findings_sha256': '6' * 64, 'provenance': 'TRUSTED_REVIEW_ADAPTER'}
        value['receipt_sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        return value

    @staticmethod
    def _legacy_trusted_review_receipt(intent, *, verdict='PASS'):
        value = TaskCompletionTests._trusted_review_receipt(intent, verdict=verdict)
        value.pop('review_packet_digest')
        value['schema_version'] = 1
        value.pop('receipt_sha256')
        value['receipt_sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        return value

    def test_reviewer_phase_recovers_without_replaying_pm_or_adapter(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        task, store, result, tests = self._direct_opencode_task('review-recovery-no-adapter')
        code, client, _, _ = self._run_direct_pm_send_to_review(
            task, store, result, tests, adapter=None)
        self.assertEqual(code, 1)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'NOT_DISPATCHED')
        self.assertEqual(len(client.calls), 1)

        # Simulate a crash after the durable PM RETURNED state but before the
        # Reviewer intent write. Recovery recreates intent without calling PM.
        developer = store.get(task['task_id'])['developer']
        callback = dict(developer['direct_result_callback'])
        callback.pop('review_handoff')
        store.update(task['task_id'], developer={**developer,
            'direct_result_callback': callback})
        with redirect_stdout(StringIO()):
            self.assertEqual(run_task_direct_result_event(
                task['task_id'], self.task_id, store=store), 1)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'NOT_DISPATCHED')
        self.assertEqual(len(client.calls), 1)

        # A persisted DISPATCHING claim is ambiguous after restart. It becomes
        # UNKNOWN and never re-enters the adapter.
        developer = store.get(task['task_id'])['developer']
        callback = dict(developer['direct_result_callback'])
        callback['review_handoff'] = task_completion.review_handoff_dispatching(
            callback['review_handoff']['intent'])
        store.update(task['task_id'], developer={**developer,
            'direct_result_callback': callback})
        adapter = unittest.mock.Mock()
        with patch('llmtool.task_submission_cli._trusted_reviewer_adapter', adapter), \
             redirect_stdout(StringIO()):
            self.assertEqual(run_task_direct_result_event(
                task['task_id'], self.task_id, store=store), 1)
        adapter.assert_not_called()
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'OUTCOME_UNKNOWN')
        self.assertEqual(len(client.calls), 1)

    def test_reviewer_adapter_error_is_persisted_unknown_and_not_replayed(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        task, store, result, tests = self._direct_opencode_task('review-adapter-error')
        adapter = unittest.mock.Mock(side_effect=RuntimeError('adapter transport failure'))
        _, client, _, _ = self._run_direct_pm_send_to_review(
            task, store, result, tests, adapter=adapter)
        self.assertEqual(adapter.call_count, 1)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'OUTCOME_UNKNOWN')
        self.assertEqual(callback['review_handoff']['error_code'],
                         'REVIEW_ADAPTER_OUTCOME_UNKNOWN')
        with patch('llmtool.task_submission_cli._trusted_reviewer_adapter', adapter), \
             redirect_stdout(StringIO()):
            self.assertEqual(run_task_direct_result_event(
                task['task_id'], self.task_id, store=store), 1)
        self.assertEqual(adapter.call_count, 1)
        self.assertEqual(len(client.calls), 1)

    def test_invalid_reviewer_receipt_is_persisted_unknown(self):
        task, store, result, tests = self._direct_opencode_task('review-invalid-receipt')
        other_intent = task_completion.review_dispatch_intent(
            task_id='9' * 32, run_id='8' * 32, contract_digest='7' * 64,
            developer_task_id='other-developer', developer_identity='other-agent',
            developer_session_ref='other-session', source_sha='6' * 40,
            result_head=None, diff_sha256='5' * 64, changed_paths=['src/other.py'])
        adapter = unittest.mock.Mock(
            return_value=task_completion.review_dispatch_blocked(other_intent))
        code, client, _, _ = self._run_direct_pm_send_to_review(
            task, store, result, tests, adapter=adapter)
        self.assertEqual(code, 1)
        self.assertEqual(adapter.call_count, 1)
        self.assertEqual(len(client.calls), 1)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'OUTCOME_UNKNOWN')
        self.assertEqual(callback['review_handoff']['error_code'], 'REVIEW_RECEIPT_INVALID')

    def test_two_events_have_one_dispatch_claimant(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        task, store, result, tests = self._direct_opencode_task('review-two-event-race')
        entered, release = threading.Event(), threading.Event()
        call_count = []

        def adapter(intent):
            call_count.append(intent['dispatch_id'])
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test adapter release timed out')
            return self._trusted_review_receipt(intent)

        first_result, first_error = [], []

        def first_event():
            try:
                first_result.append(self._run_direct_pm_send_to_review(
                    task, store, result, tests, adapter=adapter)[0])
            except BaseException as exc:
                first_error.append(exc)

        first = threading.Thread(target=first_event)
        first.start()
        self.assertTrue(entered.wait(5), 'first event did not enter Reviewer adapter')
        with patch('llmtool.task_submission_cli._trusted_reviewer_adapter', adapter), \
             redirect_stdout(StringIO()):
            second_result = run_task_direct_result_event(task['task_id'], self.task_id,
                                                         store=store)
        release.set()
        first.join(5)
        self.assertFalse(first.is_alive(), 'first Reviewer event did not finish')
        self.assertEqual(first_error, [])
        self.assertEqual(second_result, 1)
        self.assertEqual(first_result, [0])
        self.assertEqual(len(call_count), 1)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'RECEIVED')
        self.assertEqual(callback['review_return']['state'], 'RETURNED')

    def test_invalid_pm_decision_for_pass_is_rejected_and_persisted(self):
        task, store, result, tests = self._direct_opencode_task('review-invalid-pm-matrix')
        adapter = lambda intent: self._trusted_review_receipt(intent, verdict='PASS')
        code, client, _, _ = self._run_direct_pm_send_to_review(
            task, store, result, tests, adapter=adapter,
            reviewer_decision='RETURN_TO_DEVELOPER')
        self.assertEqual(code, 1)
        self.assertEqual(len(client.calls), 2)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'RECEIVED')
        self.assertEqual(callback['review_return']['state'], 'REJECTED')
        self.assertEqual(callback['review_return']['pm_error_code'],
                         'PM_RESPONSE_REJECTED')

    def test_duplicate_event_preserves_active_reviewer_dispatch_claim(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        task, store, result, tests = self._direct_opencode_task('review-dispatch-claim-race')
        persisted_claim = threading.Event()
        release_claim = threading.Event()
        adapter_entered = threading.Event()
        release_adapter = threading.Event()
        adapter_calls = []
        original_compare = store.compare_and_update

        def gated_compare(task_id, *, expected, **changes):
            saved = original_compare(task_id, expected=expected, **changes)
            proposed = changes.get('developer', {}).get('direct_result_callback', {})
            handoff = proposed.get('review_handoff')
            if type(handoff) is dict and handoff.get('dispatch_state') == 'DISPATCHING':
                persisted_claim.set()
                if not release_claim.wait(5):
                    raise RuntimeError('test claim release timed out')
            return saved

        store.compare_and_update = gated_compare

        def adapter(intent):
            adapter_calls.append(intent['dispatch_id'])
            adapter_entered.set()
            if not release_adapter.wait(5):
                raise RuntimeError('test adapter release timed out')
            return self._trusted_review_receipt(intent, verdict='PASS')

        first_result, first_errors = [], []

        def first_event():
            try:
                first_result.append(self._run_direct_pm_send_to_review(
                    task, store, result, tests, adapter=adapter)[0])
            except BaseException as exc:
                first_errors.append(exc)

        first = threading.Thread(target=first_event)
        first.start()
        self.assertTrue(persisted_claim.wait(5), 'Reviewer claim was not persisted')
        with patch('llmtool.task_submission_cli._trusted_reviewer_adapter', adapter), \
             redirect_stdout(StringIO()):
            duplicate_before_adapter = run_task_direct_result_event(
                task['task_id'], self.task_id, store=store)
        self.assertEqual(duplicate_before_adapter, 1)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'DISPATCHING')

        release_claim.set()
        self.assertTrue(adapter_entered.wait(5), 'original Reviewer adapter did not begin')
        with patch('llmtool.task_submission_cli._trusted_reviewer_adapter', adapter), \
             redirect_stdout(StringIO()):
            duplicate_during_adapter = run_task_direct_result_event(
                task['task_id'], self.task_id, store=store)
        self.assertEqual(duplicate_during_adapter, 1)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'DISPATCHING')

        release_adapter.set()
        first.join(5)
        self.assertFalse(first.is_alive(), 'original Reviewer dispatch did not finish')
        self.assertEqual(first_errors, [])
        self.assertEqual(first_result, [0])
        self.assertEqual(len(adapter_calls), 1)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'RECEIVED')
        self.assertEqual(callback['review_handoff']['receipt']['verdict'], 'PASS')
        self.assertEqual(callback['review_return']['state'], 'RETURNED')

    def test_duplicate_event_preserves_active_pm_return_call(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        task, store, result, tests = self._direct_opencode_task('review-pm-return-race')
        pm_entered, release_pm = threading.Event(), threading.Event()
        adapter_calls = []

        class BlockingPMClient:
            def __init__(self):
                self.calls = []

            def complete(self, messages):
                self.calls.append(messages)
                if len(self.calls) == 1:
                    return Reply(json.dumps({'schema_version': 1,
                        'decision': 'SEND_TO_REVIEW',
                        'summary': 'Verified result is ready.'}),
                        {'prompt_tokens': 10, 'completion_tokens': 8,
                         'total_tokens': 18})
                pm_entered.set()
                if not release_pm.wait(5):
                    raise RuntimeError('test PM release timed out')
                return Reply(json.dumps({'schema_version': 1,
                    'decision': 'CONTINUE_INTEGRATION_GATES',
                    'summary': 'Continue exact-head integration gates.'}),
                    {'prompt_tokens': 8, 'completion_tokens': 5, 'total_tokens': 13})

        client = BlockingPMClient()

        def adapter(intent):
            adapter_calls.append(intent['dispatch_id'])
            return self._trusted_review_receipt(intent, verdict='PASS')

        first_result, first_errors = [], []

        def first_event():
            try:
                first_result.append(self._run_direct_pm_send_to_review(
                    task, store, result, tests, adapter=adapter, client=client)[0])
            except BaseException as exc:
                first_errors.append(exc)

        first = threading.Thread(target=first_event)
        first.start()
        self.assertTrue(pm_entered.wait(5), 'original PM call did not begin')
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_return']['state'], 'DISPATCHING')
        with redirect_stdout(StringIO()):
            duplicate_result = run_task_direct_result_event(
                task['task_id'], self.task_id, store=store)
        self.assertEqual(duplicate_result, 1)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_return']['state'], 'DISPATCHING')

        release_pm.set()
        first.join(5)
        self.assertFalse(first.is_alive(), 'original PM call did not finish')
        self.assertEqual(first_errors, [])
        self.assertEqual(first_result, [0])
        self.assertEqual(len(adapter_calls), 1)
        self.assertEqual(len(client.calls), 2)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_return']['state'], 'RETURNED')
        self.assertEqual(callback['review_return']['next_step'],
                         'INTEGRATION_GATES_PENDING')

    def test_pending_cas_has_exactly_one_concurrent_adapter_claimant(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        task, store, result, tests = self._direct_opencode_task('review-pending-cas-race')
        _, client, _, _ = self._run_direct_pm_send_to_review(task, store, result, tests)
        saved = store.get(task['task_id'])
        developer = saved['developer']
        callback = dict(developer['direct_result_callback'])
        intent = callback['review_handoff']['intent']
        callback['review_handoff'] = task_completion.review_handoff_pending(intent)
        store.update(task['task_id'], developer={**developer,
            'direct_result_callback': callback})

        adapter_calls = []
        both_started = threading.Barrier(2)

        def adapter(request):
            adapter_calls.append(request['dispatch_id'])
            return self._trusted_review_receipt(request)

        results, errors = [], []

        def event():
            try:
                both_started.wait(timeout=5)
                with redirect_stdout(StringIO()):
                    results.append(run_task_direct_result_event(
                        task['task_id'], self.task_id, store=store))
            except BaseException as exc:
                errors.append(exc)

        with patch('llmtool.task_submission_cli._trusted_reviewer_adapter', adapter), \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   return_value=pm_config('original-pm')), \
             patch('llmtool.task_submission_cli.api_key', return_value='not-persisted'), \
             patch('llmtool.task_submission_cli.selected_provider', return_value={
                   'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None}), \
             patch('llmtool.task_submission_cli.create_client', return_value=client):
            threads = [threading.Thread(target=event) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [0, 1])
        self.assertEqual(len(adapter_calls), 1)
        self.assertEqual(len(client.calls), 2)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'RECEIVED')
        self.assertEqual(callback['review_handoff']['receipt']['verdict'], 'PASS')
        self.assertEqual(callback['review_return']['state'], 'RETURNED')
        with patch('llmtool.task_submission_cli._trusted_reviewer_adapter', adapter), \
             redirect_stdout(StringIO()):
            self.assertEqual(run_task_direct_result_event(
                task['task_id'], self.task_id, store=store), 0)
        self.assertEqual(len(adapter_calls), 1)
        self.assertEqual(len(client.calls), 2)

    def test_legacy_handoff_normalizes_and_terminal_blocked_reentry_stays_nonzero(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        task, store, result, tests = self._direct_opencode_task('review-legacy-not-dispatched')
        _, client, _, _ = self._run_direct_pm_send_to_review(task, store, result, tests)
        saved = store.get(task['task_id'])
        developer = saved['developer']
        callback = dict(developer['direct_result_callback'])
        handoff = callback['review_handoff']
        callback.pop('review_return', None)
        callback['review_handoff'] = {key: handoff[key]
            for key in ('intent', 'receipt', 'next_step')}
        store.update(task['task_id'], developer={**developer,
            'direct_result_callback': callback})
        with redirect_stdout(StringIO()):
            self.assertEqual(run_task_direct_result_event(
                task['task_id'], self.task_id, store=store), 1)
        normalized = store.get(task['task_id'])['developer']['direct_result_callback'][
            'review_handoff']
        self.assertEqual(normalized['dispatch_state'], 'NOT_DISPATCHED')
        self.assertEqual(len(client.calls), 1)

        task, store, result, tests = self._direct_opencode_task('review-received-blocked')
        adapter = lambda intent: self._trusted_review_receipt(intent, verdict='BLOCKED')
        first_code, client, _, _ = self._run_direct_pm_send_to_review(
            task, store, result, tests, adapter=adapter)
        self.assertEqual(first_code, 1)
        saved = store.get(task['task_id'])
        developer = saved['developer']
        callback = dict(developer['direct_result_callback'])
        handoff = callback['review_handoff']
        self.assertEqual(handoff['dispatch_state'], 'RECEIVED')
        with redirect_stdout(StringIO()):
            self.assertEqual(run_task_direct_result_event(
                task['task_id'], self.task_id, store=store), 1)
        normalized = store.get(task['task_id'])['developer']['direct_result_callback'][
            'review_handoff']
        self.assertEqual(normalized['dispatch_state'], 'RECEIVED')
        self.assertEqual(normalized['receipt']['verdict'], 'BLOCKED')
        self.assertEqual(len(client.calls), 2)

    def test_legacy_direct_callback_cannot_rebind_d6_agent_or_session(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        for field, replacement in (
                ('trusted_agent_id', 'replacement-agent'),
                ('session_ref', 'replacement-session'),
                ('native_session_id', 'replacement-native')):
            with self.subTest(field=field):
                task, store, _, _ = self._direct_opencode_task('legacy-' + field)
                developer = dict(task['developer'])
                developer.pop('direct_result_callback')
                task['developer'] = developer
                registration = task_completion.read_d6_registration(
                    self.task_id, self.common,
                    allowed_states=task_completion._D6_EVIDENCE_STATES)
                task['completion'] = {
                    'state': 'WAITING_EXECUTOR', 'executor_task_id': self.task_id,
                    'reviewer_task_id': None,
                    'evidence': {'task_id': task['task_id'], 'run_id': task['run_id'],
                        'contract_digest': task['task_binding']['contract_digest'],
                        'executor_identity': registration.trusted_agent_id,
                        'executor_session_ref': registration.session_ref,
                        'executor_native_session_id': registration.native_session_id,
                        'executor_native_session': 'VERIFIED',
                        'root': registration.root, 'common_dir': registration.common_dir,
                        'branch': registration.branch, 'source_sha': self.source_sha,
                        'result_head': None},
                    'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
                    'pm_response': None, 'pm_recommendation': None,
                    'pm_usage': None, 'pm_error_code': None}
                store.update(task['task_id'], developer=developer,
                             completion=task['completion'])
                registration_path = (Path(self.common) / 'llmtool-worktrees' /
                                     (self.task_id + '.json'))
                original = json.loads(registration_path.read_text())
                changed = dict(original)
                changed[field] = replacement
                registration_path.write_text(json.dumps(changed))
                registration_path.chmod(0o600)
                try:
                    with patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result') as collect, \
                            self.assertRaises(LLMToolError):
                        run_task_direct_result_event(task['task_id'], self.task_id, store=store)
                    collect.assert_not_called()
                finally:
                    registration_path.write_text(json.dumps(original))
                    registration_path.chmod(0o600)

        task, store, _, _ = self._direct_opencode_task('legacy-wrong-task-id')
        developer = dict(task['developer'])
        developer.pop('direct_result_callback')
        task['developer'] = developer
        registration = task_completion.read_d6_registration(
            self.task_id, self.common, allowed_states=task_completion._D6_EVIDENCE_STATES)
        task['completion'] = {
            'state': 'WAITING_EXECUTOR', 'executor_task_id': 'another-executor',
            'reviewer_task_id': None,
            'evidence': {'task_id': task['task_id'], 'run_id': task['run_id'],
                'contract_digest': task['task_binding']['contract_digest'],
                'executor_identity': registration.trusted_agent_id,
                'executor_session_ref': registration.session_ref,
                'executor_native_session_id': registration.native_session_id,
                'executor_native_session': 'VERIFIED', 'root': registration.root,
                'common_dir': registration.common_dir, 'branch': registration.branch,
                'source_sha': self.source_sha, 'result_head': None},
            'missing_gates': [], 'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
            'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None}
        store.update(task['task_id'], developer=developer, completion=task['completion'])
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result') as collect, \
                self.assertRaises(LLMToolError):
            run_task_direct_result_event(task['task_id'], self.task_id, store=store)
        collect.assert_not_called()

    def test_direct_callback_rechecks_worktree_after_tests_and_provider_setup(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        for drift_call, expected_gate in ((2, 'WORKTREE_SNAPSHOT_CHANGED'),
                                           (3, 'WORKTREE_CHANGED_BEFORE_PM_CALL')):
            with self.subTest(drift_call=drift_call):
                task, store, result, tests = self._direct_opencode_task(
                    'direct-drift-' + str(drift_call))
                calls = []

                def collect(**kwargs):
                    calls.append(kwargs)
                    if len(calls) == drift_call:
                        changed = dict(result)
                        changed['diff_sha256'] = '9' * 64
                        return changed
                    return result

                client = FakeClient(Reply(json.dumps({'schema_version': 1,
                    'decision': 'SEND_TO_REVIEW', 'summary': 'Ready.'}), {}))
                output = StringIO()
                with patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result',
                           side_effect=collect), \
                     patch('llmtool.task_submission_cli.task_completion.run_bounded_worktree_tests',
                           return_value=tests) as run_tests, \
                     patch('llmtool.task_submission_cli.resolve_profile_config',
                           return_value=pm_config('original-pm')) as resolve, \
                     patch('llmtool.task_submission_cli.api_key', return_value='not-persisted'), \
                     patch('llmtool.task_submission_cli.selected_provider', return_value={
                           'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None}), \
                     patch('llmtool.task_submission_cli.create_client', return_value=client), \
                     redirect_stdout(output):
                    self.assertEqual(run_task_direct_result_event(
                        task['task_id'], self.task_id, store=store), 1)
                saved = store.get(task['task_id'])['developer']['direct_result_callback']
                self.assertEqual(saved['state'], 'WAITING_EXECUTOR')
                self.assertIn(expected_gate, saved['missing_gates'])
                self.assertEqual(len(calls), drift_call)
                self.assertEqual(run_tests.call_count, 1)
                self.assertEqual(len(client.calls), 0)
                if drift_call == 2:
                    resolve.assert_not_called()

    def test_waiting_pm_retry_rechecks_after_setup_without_repeating_tests(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        task, store, result, tests = self._direct_opencode_task('direct-waiting-pm-retry')
        collector_calls = []

        def collect(**kwargs):
            collector_calls.append(kwargs)
            if len(collector_calls) == 4:
                changed = dict(result)
                changed['file_manifest_sha256'] = '8' * 64
                return changed
            return result

        resolve_error = LLMToolError('profile temporarily unavailable')
        client = FakeClient(Reply(json.dumps({'schema_version': 1,
            'decision': 'SEND_TO_REVIEW', 'summary': 'Ready.'}), {}))
        output = StringIO()
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result',
                   side_effect=collect), \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_worktree_tests',
                   return_value=tests) as run_tests, \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   side_effect=[resolve_error, pm_config('original-pm')]) as resolve, \
             patch('llmtool.task_submission_cli.api_key', return_value='not-persisted'), \
             patch('llmtool.task_submission_cli.selected_provider', return_value={
                   'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None}), \
             patch('llmtool.task_submission_cli.create_client', return_value=client), \
             redirect_stdout(output):
            self.assertEqual(run_task_direct_result_event(
                task['task_id'], self.task_id, store=store), 1)
            self.assertEqual(store.get(task['task_id'])['developer']
                ['direct_result_callback']['state'], 'WAITING_PM')
            self.assertEqual(run_task_complete(Namespace(task_id=task['task_id'],
                executor_task=self.task_id, reviewer_task=None, pr=None), store=store), 1)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['state'], 'WAITING_EXECUTOR')
        self.assertIn('WORKTREE_CHANGED_BEFORE_PM_CALL', callback['missing_gates'])
        self.assertEqual((run_tests.call_count, resolve.call_count,
                          len(collector_calls), len(client.calls)), (1, 2, 4, 0))

    def test_direct_verification_interrupt_persists_unknown_without_pm_call(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        from llmtool.providers.cli_lifecycle import CLIHostTermination
        for exception in (KeyboardInterrupt(), CLIHostTermination('host stopped')):
            with self.subTest(exception=type(exception).__name__):
                task, store, result, _ = self._direct_opencode_task(
                    'direct-test-interrupt-' + type(exception).__name__)
                output = StringIO()
                with patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result',
                           return_value=result), \
                     patch('llmtool.task_submission_cli.task_completion.run_bounded_worktree_tests',
                           side_effect=exception), \
                     patch('llmtool.task_submission_cli.resolve_profile_config') as resolve, \
                     redirect_stdout(output), self.assertRaises(type(exception)):
                    run_task_direct_result_event(task['task_id'], self.task_id, store=store)
                callback = store.get(task['task_id'])['developer']['direct_result_callback']
                self.assertEqual(callback['state'], 'WAITING_EXECUTOR')
                self.assertEqual(callback['evidence']['verification_state'], 'UNKNOWN')
                self.assertEqual(callback['pm_dispatch'], 'NOT_STARTED')
                self.assertIn('EXECUTOR_VERIFICATION_INTERRUPTED', callback['missing_gates'])
                resolve.assert_not_called()

    def test_direct_ready_persist_interrupt_releases_verification_claim_as_unknown(self):
        from llmtool.task_submission_cli import (
            _persist_direct_callback, run_task_direct_result_event)
        from llmtool.providers.cli_lifecycle import CLIHostTermination

        for exception in (KeyboardInterrupt(), CLIHostTermination('host stopped')):
            with self.subTest(exception=type(exception).__name__):
                task, store, result, tests = self._direct_opencode_task(
                    'direct-ready-persist-interrupt-' + type(exception).__name__)
                original_persist = _persist_direct_callback

                def interrupt_ready(store_arg, task_arg, developer_arg, callback):
                    if (callback.get('state') == 'WAITING_PM' and
                            callback.get('evidence', {}).get('verification_state') == 'READY'):
                        raise exception
                    return original_persist(store_arg, task_arg, developer_arg, callback)

                client = FakeClient(Reply('{"schema_version":1,"decision":"STOP",'
                                          '"summary":"Stop."}', {}))
                output = StringIO()
                with patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result',
                           return_value=result), \
                     patch('llmtool.task_submission_cli.task_completion.run_bounded_worktree_tests',
                           return_value=tests) as run_tests, \
                     patch('llmtool.task_submission_cli._persist_direct_callback',
                           side_effect=interrupt_ready), \
                     patch('llmtool.task_submission_cli.resolve_profile_config') as resolve, \
                     patch('llmtool.task_submission_cli.create_client', return_value=client), \
                     redirect_stdout(output), self.assertRaises(type(exception)):
                    run_task_direct_result_event(task['task_id'], self.task_id, store=store)
                callback = store.get(task['task_id'])['developer']['direct_result_callback']
                self.assertEqual(callback['state'], 'WAITING_EXECUTOR')
                self.assertEqual(callback['evidence']['verification_state'], 'UNKNOWN')
                self.assertEqual(callback['missing_gates'], ['EXECUTOR_VERIFICATION_INTERRUPTED'])
                self.assertEqual(callback['pm_dispatch'], 'NOT_STARTED')
                self.assertEqual(run_tests.call_count, 1)
                self.assertEqual(client.calls, [])
                resolve.assert_not_called()

    def test_direct_return_persist_interrupt_marks_unknown_and_refuses_replay(self):
        from llmtool.task_submission_cli import run_task_direct_result_event
        from llmtool.providers.cli_lifecycle import CLIHostTermination

        for exception in (KeyboardInterrupt(), CLIHostTermination('host stopped')):
            with self.subTest(exception=type(exception).__name__):
                task, store, result, tests = self._direct_opencode_task(
                    'direct-return-persist-interrupt-' + type(exception).__name__)
                client = FakeClient(Reply('{"schema_version":1,"decision":"STOP",'
                    '"summary":"Stop."}', {}))
                original_update = store.update

                def interrupt_returned(task_id, **changes):
                    callback = changes.get('developer', {}).get('direct_result_callback', {})
                    if callback.get('state') == 'PM_RETURNED':
                        raise exception
                    return original_update(task_id, **changes)

                output = StringIO()
                with patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result',
                           return_value=result), \
                     patch('llmtool.task_submission_cli.task_completion.run_bounded_worktree_tests',
                           return_value=tests), \
                     patch('llmtool.task_submission_cli.resolve_profile_config',
                           return_value=pm_config('original-pm')), \
                     patch('llmtool.task_submission_cli.api_key', return_value='not-persisted'), \
                     patch('llmtool.task_submission_cli.selected_provider', return_value={
                           'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None}), \
                     patch('llmtool.task_submission_cli.create_client', return_value=client), \
                     patch.object(store, 'update', side_effect=interrupt_returned), \
                     redirect_stdout(output), self.assertRaises(type(exception)):
                    run_task_direct_result_event(task['task_id'], self.task_id, store=store)
                callback = store.get(task['task_id'])['developer']['direct_result_callback']
                self.assertEqual(callback['state'], 'PM_OUTCOME_UNKNOWN')
                self.assertEqual(callback['pm_dispatch'], 'UNKNOWN')
                self.assertEqual(callback['pm_error_code'], 'PM_RESPONSE_PERSIST_INTERRUPTED')
                self.assertEqual(len(client.calls), 1)
                with self.assertRaises(LLMToolError):
                    run_task_direct_result_event(task['task_id'], self.task_id, store=store)
                self.assertEqual(len(client.calls), 1)

    def test_direct_pm_setup_interrupt_is_retryable_without_provider_call(self):
        from llmtool.task_submission_cli import run_task_direct_result_event
        from llmtool.providers.cli_lifecycle import CLIHostTermination

        for exception in (KeyboardInterrupt(), CLIHostTermination('host stopped')):
            with self.subTest(exception=type(exception).__name__):
                task, store, result, tests = self._direct_opencode_task(
                    'direct-pm-setup-interrupt-' + type(exception).__name__)
                client = FakeClient(Reply('{"schema_version":1,"decision":"STOP",'
                                          '"summary":"Stop."}', {}))
                output = StringIO()
                with patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result',
                           return_value=result), \
                     patch('llmtool.task_submission_cli.task_completion.run_bounded_worktree_tests',
                           return_value=tests), \
                     patch('llmtool.task_submission_cli.resolve_profile_config',
                           side_effect=exception) as resolve, \
                     patch('llmtool.task_submission_cli.create_client', return_value=client), \
                     redirect_stdout(output), self.assertRaises(type(exception)):
                    run_task_direct_result_event(task['task_id'], self.task_id, store=store)
                callback = store.get(task['task_id'])['developer']['direct_result_callback']
                self.assertEqual(callback['state'], 'WAITING_PM')
                self.assertEqual(callback['pm_dispatch'], 'NOT_STARTED')
                self.assertEqual(callback['pm_error_code'], 'PM_SETUP_INTERRUPTED')
                self.assertEqual(callback['evidence']['verification_state'], 'READY')
                resolve.assert_called_once_with('original-pm', require_configured=True)
                self.assertEqual(client.calls, [])

    def test_direct_pm_response_processing_interrupt_persists_unknown(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        task, store, result, tests = self._direct_opencode_task('direct-pm-response-interrupt')
        client = FakeClient(Reply('{"schema_version":1,"decision":"STOP",'
                                  '"summary":"Stop."}', {}))
        output = StringIO()
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result',
                   return_value=result), \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_worktree_tests',
                   return_value=tests), \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   return_value=pm_config('original-pm')), \
             patch('llmtool.task_submission_cli.api_key', return_value='not-persisted'), \
             patch('llmtool.task_submission_cli.selected_provider', return_value={
                   'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None}), \
             patch('llmtool.task_submission_cli.create_client', return_value=client), \
             patch('llmtool.task_submission_cli._pm_recommendation', side_effect=KeyboardInterrupt()), \
             redirect_stdout(output), self.assertRaises(KeyboardInterrupt):
            run_task_direct_result_event(task['task_id'], self.task_id, store=store)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['state'], 'PM_OUTCOME_UNKNOWN')
        self.assertEqual(callback['pm_dispatch'], 'UNKNOWN')
        self.assertEqual(callback['pm_error_code'], 'PM_RESPONSE_INTERRUPTED')
        self.assertEqual(len(client.calls), 1)

    def test_direct_pm_interrupt_persists_unknown_and_refuses_replay(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        task, store, result, tests = self._direct_opencode_task('direct-pm-interrupt')
        client = FakeClient(error=KeyboardInterrupt())
        output = StringIO()
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result',
                   return_value=result) as collect, \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_worktree_tests',
                   return_value=tests), \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   return_value=pm_config('original-pm')), \
             patch('llmtool.task_submission_cli.api_key', return_value='not-persisted'), \
             patch('llmtool.task_submission_cli.selected_provider', return_value={
                   'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None}), \
             patch('llmtool.task_submission_cli.create_client', return_value=client), \
             redirect_stdout(output), self.assertRaises(KeyboardInterrupt):
            run_task_direct_result_event(task['task_id'], self.task_id, store=store)
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['state'], 'PM_OUTCOME_UNKNOWN')
        self.assertEqual(callback['pm_dispatch'], 'UNKNOWN')
        self.assertEqual(callback['pm_error_code'], 'PM_CALL_INTERRUPTED')
        self.assertEqual((collect.call_count, len(client.calls)), (3, 1))
        with self.assertRaises(LLMToolError):
            run_task_direct_result_event(task['task_id'], self.task_id, store=store)
        self.assertEqual(len(client.calls), 1)

    def test_direct_pm_interrupt_keeps_interruption_if_terminal_write_or_output_fails(self):
        from llmtool.task_submission_cli import run_task_direct_result_event

        task, store, result, tests = self._direct_opencode_task('direct-pm-write-failure')
        client = FakeClient(error=KeyboardInterrupt())
        original_compare_and_update = store.compare_and_update
        update_calls = []

        def fail_terminal_update(task_id, *, expected, **changes):
            update_calls.append(changes)
            if changes.get('developer', {}).get('direct_result_callback', {}).get('state') == 'PM_OUTCOME_UNKNOWN':
                raise LLMToolError('simulated store failure')
            return original_compare_and_update(task_id, expected=expected, **changes)

        with patch('llmtool.task_submission_cli.task_completion.collect_executor_worktree_result',
                   return_value=result), \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_worktree_tests',
                   return_value=tests), \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   return_value=pm_config('original-pm')), \
             patch('llmtool.task_submission_cli.api_key', return_value='not-persisted'), \
             patch('llmtool.task_submission_cli.selected_provider', return_value={
                   'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None}), \
             patch('llmtool.task_submission_cli.create_client', return_value=client), \
             patch.object(store, 'compare_and_update', side_effect=fail_terminal_update), \
             patch('llmtool.task_submission_cli._print', side_effect=OSError('output closed')), \
             self.assertRaises(KeyboardInterrupt):
            run_task_direct_result_event(task['task_id'], self.task_id, store=store)
        terminal_attempts = [changes for changes in update_calls
            if changes.get('developer', {}).get('direct_result_callback', {}).get('state') ==
                'PM_OUTCOME_UNKNOWN']
        self.assertEqual(len(terminal_attempts), 1)
        self.assertEqual(len(client.calls), 1)

    def test_waiting_retry_preserves_immutable_d6_admission_binding(self):
        store = TaskStore(self.base / 'tasks')
        task = self._task()
        store.create(task)
        args = Namespace(task_id=task['task_id'], executor_task=self.task_id,
                         reviewer_task='reviewer-task', pr=10)
        failure = LLMToolError('evidence unavailable')
        failure.completion_code = 'RESULT_HEAD_MISMATCH'
        first_output = StringIO()
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_result',
                   side_effect=failure), redirect_stdout(first_output):
            self.assertEqual(run_task_complete(args, store=store), 1)
        waiting = store.get(task['task_id'])['completion']
        admitted = waiting['evidence']
        self.assertEqual((admitted['root'], admitted['common_dir'], admitted['branch'],
                          admitted['source_sha']),
                         (str(self.root), self.common, 'feat/executor', self.source_sha))
        result_sha = '7' * 40
        captured = {}
        def collect(**kwargs):
            captured.update(kwargs)
            return {'task_id': task['task_id'], 'run_id': task['run_id'],
                'executor_task_id': self.task_id, 'executor_identity': 'executor-agent',
                'executor_session_ref': 'Executor D6 session', 'root': str(self.root),
                'common_dir': self.common, 'branch': 'feat/executor',
                'source_sha': self.source_sha, 'result_head': result_sha,
                'changed_paths': [self.source_path], 'diff_sha256': '5' * 64}
        second_output = StringIO()
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_result',
                   side_effect=collect), \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_tests',
                   return_value=[{'path': self.source_path, 'source_sha': result_sha,
                                  'exit_code': 0, 'output_sha256': '6' * 64}]), \
             patch('llmtool.task_submission_cli.task_completion.collect_github_evidence',
                   return_value={'evidence': {}, 'missing_gates': ['FORMAL_REVIEW_MISSING']}), \
             patch('llmtool.task_submission_cli.resolve_profile_config') as resolve, \
             redirect_stdout(second_output):
            self.assertEqual(run_task_complete(args, store=store), 1)
        self.assertEqual(captured['expected_root'], str(self.root))
        self.assertEqual(captured['expected_common_dir'], self.common)
        self.assertEqual(captured['expected_branch'], 'feat/executor')
        self.assertEqual(captured['source_sha'], self.source_sha)
        self.assertEqual(store.get(task['task_id'])['completion']['state'], 'WAITING_REVIEW')
        resolve.assert_not_called()

    def test_host_worker_completion_without_persisted_receipt_waits_without_pm_or_github(self):
        store = TaskStore(self.base / 'tasks')
        task = self._task()
        task['completion']['evidence']['execution_mode'] = 'HOST_WORKER'
        task['completion']['evidence']['host_worker_receipt'] = None
        store.create(task)
        out = StringIO()
        args = Namespace(task_id=task['task_id'], executor_task=self.task_id,
                         reviewer_task='reviewer-task', pr=10)
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_result',
                   return_value={'task_id': task['task_id'], 'run_id': task['run_id'],
                       'executor_task_id': self.task_id, 'executor_identity': 'executor-agent',
                       'executor_session_ref': 'Executor D6 session', 'root': str(self.root),
                       'common_dir': self.common, 'branch': 'feat/executor',
                       'source_sha': self.source_sha, 'result_head': '4' * 40,
                       'changed_paths': [self.source_path], 'diff_sha256': '5' * 64}), \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_tests',
                   return_value=[{'path': self.source_path, 'source_sha': '4' * 40,
                                  'exit_code': 0, 'output_sha256': '6' * 64}]), \
             patch('llmtool.task_submission_cli.task_completion.collect_github_evidence') as github, \
             patch('llmtool.task_submission_cli.resolve_profile_config') as resolve, \
             redirect_stdout(out):
            self.assertEqual(run_task_complete(args, store=store), 1)
        saved = store.get(task['task_id'])
        self.assertEqual(saved['completion']['state'], 'WAITING_EXECUTOR')
        self.assertEqual(saved['completion']['pm_dispatch'], 'NOT_STARTED')
        self.assertIn('HOST_WORKER_RECEIPT_UNAVAILABLE', saved['completion']['missing_gates'])
        github.assert_not_called()
        resolve.assert_not_called()

    def test_task_store_rejects_waiting_pm_with_missing_evidence_gate(self):
        store = TaskStore(self.base / 'tasks')
        task = self._task('WAITING_PM', ['REQUIRED_CI_MISSING'])
        with self.assertRaises(LLMToolError):
            store.create(task)

    def test_task_store_rejects_unbound_review_or_unverified_native_session(self):
        for change in ('review_run_id', 'review_contract_digest', 'review_binding_verified',
                       'review_session_identity'):
            with self.subTest(change=change):
                store = TaskStore(self.base / ('tasks-' + change))
                task = self._task('WAITING_PM')
                task['completion']['reviewer_task_id'] = 'reviewer-task'
                git, tests, github = self._mock_complete_evidence(task)
                task['completion']['evidence'].update({
                    'result': git, 'tests': tests,
                    'review_pr_ci': dict(github['evidence'])})
                evidence = task['completion']['evidence']['review_pr_ci']
                if change == 'review_run_id':
                    evidence[change] = '9' * 32
                elif change == 'review_contract_digest':
                    evidence[change] = '9' * 64
                elif change == 'review_binding_verified':
                    evidence[change] = False
                else:
                    evidence[change] = 'UNKNOWN'
                with self.assertRaises(LLMToolError):
                    store.create(task)

    def _mock_complete_evidence(self, task):
        evidence = task['completion']['evidence']
        git = {'task_id': task['task_id'], 'run_id': task['run_id'],
            'executor_task_id': self.task_id, 'executor_identity': 'executor-agent',
            'executor_session_ref': evidence['executor_session_ref'],
            'root': str(self.root), 'common_dir': self.common, 'branch': 'feat/executor',
            'source_sha': self.source_sha, 'result_head': '7' * 40,
            'changed_paths': [self.source_path], 'diff_sha256': '5' * 64}
        tests = [{'path': self.source_path, 'source_sha': '7' * 40,
                  'exit_code': 0, 'output_sha256': '6' * 64}]
        gh = {'evidence': {'pr_number': 10, 'head_sha': '7' * 40,
             'formal_review_state': 'ACCEPTED', 'review_actor': 'reviewer-user',
             'reviewer_github_login': 'reviewer-user',
             'review_commit_sha': '7' * 40, 'reviewer_d6_identity': 'review-agent',
             'review_session_identity': 'D6_NATIVE_SESSION_VERIFIED',
             'review_task_id': task['task_id'], 'review_run_id': task['run_id'],
             'review_contract_digest': task['task_binding']['contract_digest'],
             'review_binding_verified': True, 'reviewer_task_id': 'reviewer-task',
             'required_checks': ['check (macOS 3.13)'],
             'successful_checks': ['check (macOS 3.13)'], 'ci_head_sha': '7' * 40},
             'missing_gates': []}
        return git, tests, gh

    def _run_with_evidence(self, task, client, *, resolver=pm_config,
                           provider=None, rotated_key='key-not-recorded'):
        store = TaskStore(self.base / 'tasks')
        store.create(task)
        git, tests, gh = self._mock_complete_evidence(task)
        out = StringIO()
        args = Namespace(task_id=task['task_id'], executor_task=self.task_id,
                         reviewer_task='reviewer-task', pr=10)
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_result',
                   return_value=git), \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_tests',
                   return_value=tests), \
             patch('llmtool.task_submission_cli.task_completion.collect_github_evidence',
                   return_value=gh), \
             patch('llmtool.task_submission_cli.resolve_profile_config', side_effect=resolver) as resolve, \
             patch('llmtool.task_submission_cli.api_key', return_value=rotated_key), \
             patch('llmtool.task_submission_cli.selected_provider', return_value=(provider or {
                   'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None})), \
             patch('llmtool.task_submission_cli.create_client', return_value=client), \
             redirect_stdout(out):
            code = run_task_complete(args, store=store)
        return code, json.loads(out.getvalue()), store, resolve

    def test_original_pm_profile_receives_evidence_once_and_recommendation_persists(self):
        task = self._task()
        response = json.dumps({'schema_version': 1, 'decision': 'ACCEPT',
                               'summary': 'Evidence packet is ready for PM disposition.'})
        client = FakeClient(Reply(response, {'prompt_tokens': 11,
            'completion_tokens': 7, 'total_tokens': 18}))
        code, result, store, resolve = self._run_with_evidence(task, client)
        self.assertEqual(code, 0)
        self.assertEqual(result['completion']['state'], 'PM_RETURNED')
        self.assertEqual(result['completion']['pm_recommendation']['decision'], 'ACCEPT')
        resolve.assert_called_once_with('original-pm', require_configured=True)
        self.assertEqual(len(client.calls), 1)
        packet = json.loads(client.calls[0][1]['content'])
        self.assertEqual(packet['result_head'], '7' * 40)
        self.assertEqual(packet['pm_assignment']['goal'], 'Add bounded test.')
        self.assertEqual(packet['pm_assignment']['acceptance'], ['test passes'])
        self.assertNotIn('artifact', packet)
        self.assertNotIn('patch', packet)
        args = Namespace(task_id=task['task_id'], executor_task=self.task_id,
                         reviewer_task='reviewer-task', pr=10)
        with self.assertRaises(LLMToolError):
            run_task_complete(args, store=store)
        self.assertEqual(len(client.calls), 1)

    def test_changed_endpoint_for_same_profile_type_and_model_is_rejected_before_call(self):
        task = self._task()
        client = FakeClient(Reply('{"schema_version":1,"decision":"ACCEPT",'
                                  '"summary":"Evidence is ready."}', {}))
        code, result, _, _ = self._run_with_evidence(task, client, provider={
            'type': 'openai-compatible', 'model': 'pm-model',
            'base_url': 'https://different.example'})
        self.assertEqual(code, 1)
        self.assertEqual(result['completion']['state'], 'WAITING_PM')
        self.assertEqual(result['completion']['pm_error_code'], 'PM_PROVIDER_UNAVAILABLE')
        self.assertEqual(client.calls, [])

    def test_secret_rotation_is_not_bound_or_persisted(self):
        task = self._task()
        rotated_secret = 'rotated-secret-never-persist-this-value'
        client = FakeClient(Reply('{"schema_version":1,"decision":"ACCEPT",'
                                  '"summary":"Evidence is ready."}', {}))
        code, result, store, _ = self._run_with_evidence(
            task, client, rotated_key=rotated_secret)
        self.assertEqual(code, 0)
        saved = store.get(task['task_id'])
        serialized = json.dumps(saved, sort_keys=True)
        self.assertNotIn(rotated_secret, serialized)
        self.assertNotIn('api_key', serialized.lower())
        self.assertEqual(saved['pm_route_binding'], {
            'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None})

    def test_original_pm_unavailable_retains_evidence_without_provider_call(self):
        task = self._task()
        client = FakeClient(Reply('{"schema_version":1,"decision":"STOP","summary":"stop"}', {}))
        def unavailable(*args, **kwargs):
            raise LLMToolError('PM profile unavailable')
        code, result, _, resolve = self._run_with_evidence(task, client, resolver=unavailable)
        self.assertEqual(code, 1)
        self.assertEqual(result['completion']['state'], 'WAITING_PM')
        self.assertEqual(result['completion']['pm_dispatch'], 'NOT_STARTED')
        self.assertEqual(result['completion']['missing_gates'], [])
        self.assertIn('result', result['completion']['evidence'])
        self.assertEqual(resolve.call_args.args[0], 'original-pm')
        self.assertEqual(client.calls, [])

    def test_invalid_pm_recommendation_is_persisted_as_rejected_and_never_replayed(self):
        task = self._task()
        client = FakeClient(Reply('not a recommendation schema', {'total_tokens': 2}))
        code, result, store, _ = self._run_with_evidence(task, client)
        self.assertEqual(code, 1)
        self.assertEqual(result['completion']['state'], 'PM_RESPONSE_REJECTED')
        self.assertEqual(result['completion']['pm_dispatch'], 'RETURNED')
        self.assertEqual(result['completion']['pm_error_code'], 'PM_RESPONSE_REJECTED')
        self.assertIsNone(result['completion']['pm_recommendation'])
        self.assertEqual(result['completion']['pm_response'], 'not a recommendation schema')
        args = Namespace(task_id=task['task_id'], executor_task=self.task_id,
                         reviewer_task='reviewer-task', pr=10)
        with self.assertRaises(LLMToolError):
            run_task_complete(args, store=store)
        self.assertEqual(len(client.calls), 1)

    def test_ambiguous_original_pm_result_is_unknown_and_never_replayed(self):
        task = self._task()
        client = FakeClient(error=RuntimeError('ambiguous transport outcome'))
        code, result, store, _ = self._run_with_evidence(task, client)
        self.assertEqual(code, 1)
        self.assertEqual(result['completion']['state'], 'PM_OUTCOME_UNKNOWN')
        self.assertEqual(result['completion']['pm_dispatch'], 'UNKNOWN')
        self.assertIn('result', result['completion']['evidence'])
        args = Namespace(task_id=task['task_id'], executor_task=self.task_id,
                         reviewer_task='reviewer-task', pr=10)
        with self.assertRaises(LLMToolError):
            run_task_complete(args, store=store)
        self.assertEqual(len(client.calls), 1)

    def test_reviewer_handoff_receipts_are_bound_and_next_step_is_bounded(self):
        intent = task_completion.review_dispatch_intent(
            task_id='1' * 32, run_id='2' * 32, contract_digest='3' * 64,
            developer_task_id='developer-task', developer_identity='developer-agent',
            developer_session_ref='developer-session', source_sha='4' * 40,
            result_head=None, diff_sha256='5' * 64,
            changed_paths=['src/a.py'])
        blocked = task_completion.review_dispatch_blocked(intent)
        self.assertEqual(task_completion.review_handoff_record(intent, blocked)['next_step'],
                         'WAIT_FOR_REVIEWER')

        for execution_status in ('FAILED', 'BLOCKED', 'UNKNOWN'):
            uncertain = {'schema_version': 2,
                'dispatch_id': intent['dispatch_id'], 'task_id': intent['task_id'],
                'run_id': intent['run_id'], 'contract_digest': intent['contract_digest'],
                'source_sha': intent['source_sha'], 'result_head': intent['result_head'],
                'diff_sha256': intent['diff_sha256'],
                'developer_task_id': intent['developer_task_id'],
                'reviewer_task_id': 'reviewer-task',
                'reviewer_identity': 'reviewer-agent',
                'reviewer_session_ref': 'reviewer-session',
                'review_packet_digest': '7' * 64, 'verdict': 'BLOCKED',
                'security': 'BLOCKED', 'functional': 'BLOCKED',
                'execution_status': execution_status, 'code': 'REVIEW_UNAVAILABLE',
                'findings_sha256': '6' * 64,
                'provenance': 'TRUSTED_REVIEW_ADAPTER'}
            uncertain['receipt_sha256'] = hashlib.sha256(json.dumps(
                uncertain, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            self.assertEqual(task_completion.review_handoff_record(
                intent, uncertain)['next_step'], 'WAIT_FOR_REVIEWER')

        def receipt(verdict, security, functional, repair_round=0):
            current = dict(intent)
            if repair_round:
                current = task_completion.review_dispatch_intent(
                    task_id=intent['task_id'], run_id=intent['run_id'],
                    contract_digest=intent['contract_digest'],
                    developer_task_id=intent['developer_task_id'],
                    developer_identity=intent['developer_identity'],
                    developer_session_ref=intent['developer_session_ref'],
                    source_sha=intent['source_sha'], result_head=intent['result_head'],
                    diff_sha256=intent['diff_sha256'], changed_paths=intent['changed_paths'],
                    repair_round=repair_round)
            value = {'schema_version': 2, 'dispatch_id': current['dispatch_id'],
                'task_id': current['task_id'], 'run_id': current['run_id'],
                'contract_digest': current['contract_digest'], 'source_sha': current['source_sha'],
                'result_head': current['result_head'], 'diff_sha256': current['diff_sha256'],
                'developer_task_id': current['developer_task_id'],
                'reviewer_task_id': 'reviewer-task', 'reviewer_identity': 'reviewer-agent',
                'reviewer_session_ref': 'reviewer-session',
                'review_packet_digest': '7' * 64, 'verdict': verdict,
                'security': security, 'functional': functional, 'execution_status': 'SUCCEEDED',
                'code': 'REVIEW_COMPLETED', 'findings_sha256': '6' * 64,
                'provenance': 'TRUSTED_REVIEW_ADAPTER'}
            value['receipt_sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True,
                separators=(',', ':')).encode()).hexdigest()
            return current, value

        passed_intent, passed = receipt('PASS', 'PASS', 'PASS')
        self.assertEqual(task_completion.review_handoff_record(passed_intent, passed)[
            'next_step'], 'INTEGRATION_GATES_PENDING')
        legacy = dict(passed)
        legacy.pop('review_packet_digest')
        legacy['schema_version'] = 1
        legacy.pop('receipt_sha256')
        legacy['receipt_sha256'] = hashlib.sha256(json.dumps(
            legacy, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(task_completion.validate_review_dispatch_receipt(
            passed_intent, legacy), legacy)
        failed_intent, failed = receipt('FAIL', 'PASS', 'FAIL')
        self.assertEqual(task_completion.review_handoff_record(failed_intent, failed)[
            'next_step'], 'RETURN_TO_DEVELOPER_ONCE')
        repaired_intent, repaired = receipt('FAIL', 'PASS', 'FAIL', repair_round=1)
        self.assertEqual(task_completion.review_handoff_record(repaired_intent, repaired)[
            'next_step'], 'STOP_REPAIR_LIMIT')
        passed_handoff = task_completion.review_handoff_record(passed_intent, passed)
        self.assertEqual(task_completion.review_return_record(passed_handoff,
            state='RETURNED', response='{}', recommendation={
                'decision': 'CONTINUE_INTEGRATION_GATES', 'summary': 'CI gates pending.'
            })['next_step'], 'INTEGRATION_GATES_PENDING')
        self.assertEqual(task_completion.review_return_record(passed_handoff,
            state='RETURNED', response='{}', recommendation={
                'decision': 'STOP', 'summary': 'PM stopped the workflow.'
            })['next_step'], 'STOP')
        failed_handoff = task_completion.review_handoff_record(failed_intent, failed)
        failed_return = task_completion.review_return_record(failed_handoff,
            state='RETURNED', response='{}', recommendation={
                'decision': 'RETURN_TO_DEVELOPER', 'summary': 'One bounded repair.'
            })
        self.assertEqual(failed_return['next_step'], 'RETURN_TO_DEVELOPER_ONCE')
        with patch.object(task_completion.subprocess, 'Popen',
                          side_effect=AssertionError('repair intent must not launch')):
            repair_intent = task_completion.review_repair_intent(
                failed_handoff, failed_return)
            self.assertEqual(task_completion.review_repair_intent(
                failed_handoff, failed_return), repair_intent)
            self.assertIs(task_completion.validate_review_repair_intent(
                repair_intent, failed_handoff, failed_return), repair_intent)
        self.assertEqual(repair_intent['task_id'], failed_intent['task_id'])
        self.assertEqual(repair_intent['run_id'], failed_intent['run_id'])
        self.assertEqual(repair_intent['contract_digest'], failed_intent['contract_digest'])
        self.assertEqual(repair_intent['source_sha'], failed_intent['source_sha'])
        self.assertEqual(repair_intent['result_head'], failed_intent['result_head'])
        self.assertEqual(repair_intent['diff_sha256'], failed_intent['diff_sha256'])
        self.assertEqual(repair_intent['changed_paths'], failed_intent['changed_paths'])
        self.assertEqual(repair_intent['developer_task_id'], failed_intent['developer_task_id'])
        self.assertEqual(repair_intent['developer_identity'], failed_intent['developer_identity'])
        self.assertEqual(repair_intent['developer_session_ref'],
                         failed_intent['developer_session_ref'])
        self.assertEqual(repair_intent['review_dispatch_id'], failed_intent['dispatch_id'])
        self.assertEqual(repair_intent['review_receipt_sha256'], failed['receipt_sha256'])
        self.assertEqual((repair_intent['prior_repair_round'], repair_intent['repair_round']),
                         (0, 1))
        self.assertEqual(repair_intent['decision'], 'RETURN_TO_DEVELOPER')
        self.assertEqual(repair_intent['state'], 'REQUESTED')

        stale = dict(repair_intent, source_sha='8' * 40)
        stale.pop('intent_sha256')
        stale['intent_sha256'] = hashlib.sha256(json.dumps(
            stale, ensure_ascii=True, sort_keys=True,
            separators=(',', ':')).encode('utf-8')).hexdigest()
        with self.assertRaisesRegex(LLMToolError, 'details omitted') as caught:
            task_completion.validate_review_repair_intent(
                stale, failed_handoff, failed_return)
        self.assertEqual(caught.exception.completion_code,
                         'REVIEW_REPAIR_BINDING_MISMATCH')

        bad_digest = dict(repair_intent, state='CONSUMED')
        with self.assertRaises(LLMToolError) as caught:
            task_completion.validate_review_repair_intent(
                bad_digest, failed_handoff, failed_return)
        self.assertEqual(caught.exception.completion_code,
                         'REVIEW_REPAIR_DIGEST_MISMATCH')

        forged_id = dict(repair_intent, repair_intent_id='0' * 64)
        forged_id.pop('intent_sha256')
        forged_id['intent_sha256'] = hashlib.sha256(json.dumps(
            forged_id, ensure_ascii=True, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
        with self.assertRaises(LLMToolError) as caught:
            task_completion.validate_review_repair_intent(
                forged_id, failed_handoff, failed_return)
        self.assertEqual(caught.exception.completion_code,
                         'REVIEW_REPAIR_BINDING_MISMATCH')

        unserializable = dict(repair_intent, schema_version=object())
        with self.assertRaises(LLMToolError) as caught:
            task_completion.validate_review_repair_intent(
                unserializable, failed_handoff, failed_return)
        self.assertEqual(caught.exception.completion_code,
                         'REVIEW_REPAIR_BINDING_INVALID')

        pending_return = task_completion.review_return_record(failed_handoff)
        with self.assertRaises(LLMToolError) as caught:
            task_completion.review_repair_intent(failed_handoff, pending_return)
        self.assertEqual(caught.exception.completion_code,
                         'REVIEW_REPAIR_BINDING_INVALID')
        stopped_return = task_completion.review_return_record(failed_handoff,
            state='RETURNED', response='{}', recommendation={
                'decision': 'STOP', 'summary': 'PM stopped the repair.'})
        with self.assertRaises(LLMToolError) as caught:
            task_completion.review_repair_intent(failed_handoff, stopped_return)
        self.assertEqual(caught.exception.completion_code,
                         'REVIEW_REPAIR_BINDING_INVALID')

        blocked_handoff = task_completion.review_handoff_record(intent, blocked)
        repaired_handoff = task_completion.review_handoff_record(repaired_intent, repaired)
        for other_handoff, other_return in (
                (passed_handoff, task_completion.review_return_record(
                    passed_handoff, state='RETURNED', response='{}', recommendation={
                        'decision': 'STOP', 'summary': 'No repair.'})),
                (repaired_handoff, task_completion.review_return_record(
                    repaired_handoff, state='RETURNED', response='{}', recommendation={
                        'decision': 'STOP', 'summary': 'Repair limit reached.'})),
                (blocked_handoff, task_completion.review_return_record(
                    blocked_handoff, state='RETURNED', response='{}', recommendation={
                        'decision': 'WAIT_FOR_REVIEWER', 'summary': 'No review result.'})),
        ):
            with self.subTest(verdict=other_handoff['receipt']['verdict'],
                              repair_round=other_handoff['intent']['repair_round']):
                with self.assertRaises(LLMToolError) as caught:
                    task_completion.review_repair_intent(other_handoff, other_return)
                self.assertEqual(caught.exception.completion_code,
                                 'REVIEW_REPAIR_BINDING_INVALID')
        self.assertEqual(task_completion.review_return_record(failed_handoff,
            state='RETURNED', response='{}', recommendation={
                'decision': 'STOP', 'summary': 'PM stopped after the failure.'
            })['next_step'], 'STOP')
        self.assertEqual(task_completion.review_return_record(blocked_handoff,
            state='RETURNED', response='{}', recommendation={
                'decision': 'WAIT_FOR_REVIEWER', 'summary': 'Reviewer is blocked.'
            })['next_step'], 'WAIT_FOR_REVIEWER')
        self.assertEqual(task_completion.review_return_record(blocked_handoff,
            state='RETURNED', response='{}', recommendation={
                'decision': 'STOP', 'summary': 'PM stopped while blocked.'
            })['next_step'], 'STOP')

        def reject(handoff, decision):
            with self.subTest(verdict=handoff['receipt']['verdict'],
                              repair_round=handoff['intent']['repair_round'],
                              decision=decision):
                with self.assertRaises(LLMToolError):
                    task_completion.review_return_record(handoff, state='RETURNED',
                        response='{}', recommendation={'decision': decision,
                                                       'summary': 'Matrix rejection.'})

        for decision in ('RETURN_TO_DEVELOPER', 'WAIT_FOR_REVIEWER'):
            reject(passed_handoff, decision)
        for decision in ('CONTINUE_INTEGRATION_GATES', 'WAIT_FOR_REVIEWER'):
            reject(failed_handoff, decision)
        for decision in ('CONTINUE_INTEGRATION_GATES', 'RETURN_TO_DEVELOPER',
                         'WAIT_FOR_REVIEWER'):
            reject(repaired_handoff, decision)
        for decision in ('CONTINUE_INTEGRATION_GATES', 'RETURN_TO_DEVELOPER'):
            reject(blocked_handoff, decision)
        all_decisions = ('CONTINUE_INTEGRATION_GATES', 'RETURN_TO_DEVELOPER',
                         'WAIT_FOR_REVIEWER', 'STOP')
        expected = {
            ('PASS', 0): {'CONTINUE_INTEGRATION_GATES', 'STOP'},
            ('PASS', 1): {'CONTINUE_INTEGRATION_GATES', 'STOP'},
            ('FAIL', 0): {'RETURN_TO_DEVELOPER', 'STOP'},
            ('FAIL', 1): {'STOP'},
            ('BLOCKED', 0): {'WAIT_FOR_REVIEWER', 'STOP'},
            ('BLOCKED', 1): {'WAIT_FOR_REVIEWER', 'STOP'},
        }
        for (verdict, repair_round), allowed in expected.items():
            for decision in all_decisions:
                with self.subTest(verdict=verdict, repair_round=repair_round,
                                  decision=decision):
                    self.assertEqual(task_completion.review_return_decision_allowed(
                        verdict, repair_round, decision), decision in allowed)
        self.assertFalse(task_completion.review_return_decision_allowed('PASS', True,
                                                                        'STOP'))
        forged = dict(passed)
        forged['source_sha'] = '7' * 40
        forged.pop('receipt_sha256')
        forged['receipt_sha256'] = hashlib.sha256(json.dumps(forged, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        with self.assertRaises(LLMToolError):
            task_completion.validate_review_dispatch_receipt(passed_intent, forged)

    def test_malformed_circular_and_deep_handoff_return_inputs_have_stable_errors(self):
        intent = task_completion.review_dispatch_intent(
            task_id='1' * 32, run_id='2' * 32, contract_digest='3' * 64,
            developer_task_id='developer-task', developer_identity='developer-agent',
            developer_session_ref='developer-session', source_sha='4' * 40,
            result_head=None, diff_sha256='5' * 64, changed_paths=['src/a.py'])
        blocked = task_completion.review_dispatch_blocked(intent)

        circular_intent = dict(intent)
        circular_intent['developer_identity'] = circular_intent
        with self.assertRaises(LLMToolError) as caught:
            task_completion.validate_review_dispatch_intent(circular_intent)
        self.assertEqual(caught.exception.completion_code,
                         'REVIEW_DISPATCH_BINDING_INVALID')

        deep_value = []
        cursor = deep_value
        for _ in range(65):
            nested = []
            cursor.append(nested)
            cursor = nested
        deep_receipt = dict(blocked, reviewer_identity=deep_value)
        with self.assertRaises(LLMToolError) as caught:
            task_completion.validate_review_dispatch_receipt(intent, deep_receipt)
        self.assertEqual(caught.exception.completion_code, 'REVIEW_RECEIPT_INVALID')

        handoff = task_completion.review_handoff_record(intent, blocked)
        with self.assertRaises(LLMToolError) as caught:
            task_completion.review_return_record({'dispatch_state': 'RECEIVED'})
        self.assertEqual(caught.exception.completion_code, 'REVIEW_RECEIPT_INVALID')

        with self.assertRaises(LLMToolError) as caught:
            task_completion.review_return_record(handoff, state=[])
        self.assertEqual(caught.exception.completion_code, 'REVIEW_RECEIPT_INVALID')

        with self.assertRaises(LLMToolError) as caught:
            task_completion.validate_review_return_record({'state': 'RETURNED'}, handoff)
        self.assertEqual(caught.exception.completion_code, 'REVIEW_RECEIPT_INVALID')

        cyclic_usage = {}
        cyclic_usage['self'] = cyclic_usage
        with self.assertRaises(LLMToolError) as caught:
            task_completion.review_return_record(
                handoff, state='RETURNED', response='{}',
                recommendation={'decision': 'WAIT_FOR_REVIEWER', 'summary': 'Waiting.'},
                usage=cyclic_usage)
        self.assertEqual(caught.exception.completion_code, 'REVIEW_RECEIPT_INVALID')

        circular_return = {
            'state': 'RETURNED', 'receipt_sha256': blocked['receipt_sha256'],
            'next_step': 'WAIT_FOR_REVIEWER', 'pm_dispatch': 'RETURNED',
            'pm_response': '{}',
            'pm_recommendation': {'decision': 'WAIT_FOR_REVIEWER', 'summary': 'Waiting.'},
            'pm_usage': None,
            'pm_error_code': None,
        }
        circular_return['pm_recommendation']['self'] = circular_return
        with self.assertRaises(LLMToolError) as caught:
            task_completion.validate_review_return_record(circular_return, handoff)
        self.assertEqual(caught.exception.completion_code, 'REVIEW_RECEIPT_INVALID')

        cyclic_handoff = dict(handoff)
        cyclic_handoff['intent'] = cyclic_handoff
        with self.assertRaises(LLMToolError) as caught:
            task_completion.review_repair_intent(cyclic_handoff, {})
        self.assertEqual(caught.exception.completion_code,
                         'REVIEW_DISPATCH_BINDING_INVALID')

    def test_json_boundary_maps_only_expected_serialization_errors(self):
        intent = task_completion.review_dispatch_intent(
            task_id='1' * 32, run_id='2' * 32, contract_digest='3' * 64,
            developer_task_id='developer-task', developer_identity='developer-agent',
            developer_session_ref='developer-session', source_sha='4' * 40,
            result_head=None, diff_sha256='5' * 64, changed_paths=['src/a.py'])
        for exception in (TypeError, ValueError, OverflowError, RecursionError,
                          UnicodeError):
            with self.subTest(exception=exception.__name__):
                with patch.object(task_completion.json, 'dumps', side_effect=exception):
                    with self.assertRaises(LLMToolError) as caught:
                        task_completion.validate_review_dispatch_intent(intent)
                self.assertEqual(caught.exception.completion_code,
                                 'REVIEW_DISPATCH_BINDING_INVALID')

        with patch.object(task_completion.json, 'dumps',
                          side_effect=AssertionError('unexpected encoder failure')):
            with self.assertRaisesRegex(AssertionError, 'unexpected encoder failure'):
                task_completion.validate_review_dispatch_intent(intent)

    def test_repair_intent_rejects_numeric_type_confusion_and_bad_digest_shape(self):
        intent = task_completion.review_dispatch_intent(
            task_id='1' * 32, run_id='2' * 32, contract_digest='3' * 64,
            developer_task_id='developer-task', developer_identity='developer-agent',
            developer_session_ref='developer-session', source_sha='4' * 40,
            result_head=None, diff_sha256='5' * 64, changed_paths=['src/a.py'])
        receipt = {
            'schema_version': 2, 'dispatch_id': intent['dispatch_id'],
            'task_id': intent['task_id'], 'run_id': intent['run_id'],
            'contract_digest': intent['contract_digest'], 'source_sha': intent['source_sha'],
            'result_head': intent['result_head'], 'diff_sha256': intent['diff_sha256'],
            'developer_task_id': intent['developer_task_id'],
            'reviewer_task_id': 'reviewer-task', 'reviewer_identity': 'reviewer-agent',
            'reviewer_session_ref': 'reviewer-session', 'review_packet_digest': '6' * 64,
            'verdict': 'FAIL', 'security': 'PASS', 'functional': 'FAIL',
            'execution_status': 'SUCCEEDED', 'code': 'REVIEW_COMPLETED',
            'findings_sha256': '7' * 64, 'provenance': 'TRUSTED_REVIEW_ADAPTER',
        }
        receipt['receipt_sha256'] = hashlib.sha256(json.dumps(
            receipt, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        handoff = task_completion.review_handoff_record(intent, receipt)
        review_return = task_completion.review_return_record(
            handoff, state='RETURNED', response='{}', recommendation={
                'decision': 'RETURN_TO_DEVELOPER', 'summary': 'One bounded repair.'})
        repair_intent = task_completion.review_repair_intent(handoff, review_return)

        noncanonical = dict(repair_intent, schema_version=True)
        noncanonical.pop('intent_sha256')
        noncanonical['intent_sha256'] = hashlib.sha256(json.dumps(
            noncanonical, ensure_ascii=True, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        with self.assertRaises(LLMToolError) as caught:
            task_completion.validate_review_repair_intent(
                noncanonical, handoff, review_return)
        self.assertEqual(caught.exception.completion_code,
                         'REVIEW_REPAIR_BINDING_MISMATCH')

        bad_digest = dict(repair_intent, intent_sha256=True)
        with self.assertRaises(LLMToolError) as caught:
            task_completion.validate_review_repair_intent(bad_digest, handoff, review_return)
        self.assertEqual(caught.exception.completion_code, 'REVIEW_REPAIR_DIGEST_MISMATCH')


    def _claude_bound_task_context(self, *, skip_remote_test=False,
                                   skip_completed_claim=False, old_sha_receipt=False,
                                   guard_file=False):
        from llmtool import remote_test, review_dispatch
        from llmtool.cli import main

        d6_task = '4' * 32
        owner_ref = ('https://github.com/davidcatcat/davidcattoolsproject/'
                     'issues/289#issuecomment-6641326482')
        root = self.base / 'claude-bound-executor'
        root.mkdir(mode=0o700)
        self.root = root
        self.common = str(root / '.git')
        self.task_id = d6_task
        self._git('init', '-b', 'feat/executor')
        (Path(self.common) / 'llmtool-worktrees').mkdir(mode=0o700)
        self._git('config', 'user.name', 'Executor')
        self._git('config', 'user.email', 'executor@example.invalid')
        self.source_path = 'tools/llmtool/tests/test_fixture.py'
        source = root / self.source_path
        source.parent.mkdir(parents=True, exist_ok=True)
        (root / 'tools' / 'llmtool' / '.gitignore').write_text(
            '/.d6-bootstrap-report.json\n')
        source.write_text('import unittest\nclass Fixture(unittest.TestCase):\n'
                          '    def test_ok(self): self.assertTrue(True)\n')
        (root / 'README.md').write_text('Repository fixture.\n')
        self._git('add', self.source_path, 'tools/llmtool/.gitignore', 'README.md')
        if guard_file:
            self._git('commit', '-m', 'pre-source')
            (root / 'guard.txt').write_text(
                ''.join(f'guard line {index}\n' for index in range(30)))
            self._git('add', 'guard.txt')
        self._git('commit', '-m', 'source')
        self.source_sha = self._git('rev-parse', 'HEAD')
        base = self.source_sha
        self._git('branch', 'main', base)
        self._write_registration(d6_task, 'claude-developer', 'claude-session',
            root, 'feat/executor', base, base, native_session_id='claude-native')
        head, _ = self._make_result()

        policy = json.loads(self.policy_path.read_text(encoding='utf-8'))
        policy['workers']['claude-developer']['dispatch'] = 'OWNER_ASSIGNMENT_ONLY'
        policy['workers']['claude-developer']['owner_assignments'] = [{
            'task_id': d6_task, 'owner_assignment_ref': owner_ref}]
        policy['workers']['codex-reviewer']['dispatch'] = 'ALLOWED'
        self.policy_path.write_text(json.dumps(policy), encoding='utf-8')

        home = self.base / 'home'
        home.mkdir(mode=0o700)
        remote_config = home / '.config' / 'llmtool' / 'remote-test.json'
        remote_config.parent.mkdir(parents=True, mode=0o700)
        remote_config.write_text(json.dumps({
            'host': 'ec2.example.org', 'user': 'runner',
            'repo_path': '/srv/repo', 'python': '/usr/bin/python3'}))
        remote_config.chmod(0o600)
        tree = self._git('rev-parse', head + '^{tree}')
        receipt = {
            'result': 'PASS', 'exit_code': 0, 'source_sha': head,
            'tree': tree, 'fetch_head': head, 'modules': ['test_fixture'],
            'identity': {'uid': 65534}, 'started_at': '2026-11-05T00:00:00Z',
            'ended_at': '2026-11-05T00:00:01Z', 'elapsed_s': 1.0,
            'ran_total': 1, 'final': ['OK'], 'failures': [], 'log_bytes': 0,
            'log_sha256': 'd' * 64,
            'remote_log_path': '/tmp/llmtool-remote-log.Abc123/remote-test.log',
        }

        def call(argv, stdin=''):
            output = StringIO()
            with patch.dict(os.environ, {
                    'HOME': str(home), 'PATH': '/usr/bin:/bin',
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
                    clear=True), \
                    patch('sys.stdin', StringIO(stdin)), redirect_stdout(output), \
                    patch('llmtool.source_freshness.require_fresh_source',
                          return_value='OK'):
                rc = main(argv)
            rendered = output.getvalue()
            return rc, json.loads(rendered) if rendered.strip() else None

        envelope = json.dumps({
            'request': 'Verify the bounded change.',
            'response': json.dumps({'schema_version': 1, 'summary': 'Bind Claude.',
                'developer_assignment': {'route': 'claude', 'risk': 'LOW',
                    'goal': 'Record the result.',
                    'acceptance': ['The result is verified.']}}),
        })
        with patch.dict(os.environ, {
                'HOME': str(home), 'PATH': '/usr/bin:/bin',
                'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
                clear=True):
            store = TaskStore()
            self.assertEqual(store.root, home / '.local' / 'state' /
                             'llmtool' / 'tasks')
            rc, recorded = call(['task', 'record-pm', '--stdin-json'], envelope)
            self.assertEqual(rc, 0)
            task = store.get(recorded['task_id'])
            task_id, run_id = task['task_id'], task['run_id']
            contract_digest = task['task_binding']['contract_digest']

            rc, _ = call(['task', 'claude-bind', task_id, '--executor-task',
                          d6_task, '--owner-ref', owner_ref, '--common-dir', self.common,
                          '--read-path', self.source_path,
                          '--write-path', self.source_path])
            self.assertEqual(rc, 0)
            if not old_sha_receipt:
                rc, _ = call(['task', 'claude-result', task_id, '--executor-task',
                              d6_task, '--head', head,
                              '--pm-session', task['pm_session_id']])
                self.assertEqual(rc, 0)

            real_remote_test = remote_test.run_remote_test

            def run_with_ssh(*args, **kwargs):
                def ssh(argv, script, timeout):
                    return subprocess.CompletedProcess(
                        argv, 0, json.dumps(receipt).encode(), b'')
                return real_remote_test(*args, transport=ssh, **kwargs)

            if not skip_remote_test:
                with patch.object(remote_test, 'run_remote_test', side_effect=run_with_ssh):
                    rc, remote = call(['task', 'remote-test', task_id,
                        '--executor-task', d6_task, '--sha', head,
                        '--ref', 'refs/heads/feat/executor', '--module', 'test_fixture'])
                self.assertIsNone(rc)
                self.assertEqual(remote['result'], 'PASS')

            if old_sha_receipt:
                head, _ = self._make_result(
                    code='    def test_ok(self): self.assertEqual(2, 2)\n')
                rc, _ = call(['task', 'claude-result', task_id, '--executor-task',
                              d6_task, '--head', head,
                              '--pm-session', task['pm_session_id']])
                self.assertEqual(rc, 0)

            pr = {'number': 10, 'state': 'open', 'merged': False,
                  'head': {'sha': head}, 'base': {'sha': base, 'repo': {
                      'full_name': 'davidcatcat/davidcattoolsproject'}},
                  'user': {'login': 'author-user'}}
            body = (
                '<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n'
                f'Verdict: PASS | Version: v1 | HEAD: {head}\n'
                'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
                f'LLMTOOL-REVIEW-BINDING v1 task={task_id} run={run_id} '
                f'contract={contract_digest} head={head}')

            def create_d6(**kw):
                reviewer_root = self.base / kw['task_id']
                self._git('worktree', 'add', '-b',
                    'llmtool/reviewer-' + kw['task_id'], str(reviewer_root), kw['base_sha'])
                self._write_registration(kw['task_id'], kw['reviewer_key'],
                    'pending-' + kw['task_id'], reviewer_root,
                    'llmtool/reviewer-' + kw['task_id'], kw['base_sha'], kw['base_sha'],
                    github_login=kw['github_login'], state='REVIEW_PENDING')

            def bind_d6(task_id, session_id, repo_root):
                registry = Path(self.common) / 'llmtool-worktrees' / (task_id + '.json')
                value = json.loads(registry.read_text(encoding='utf-8'))
                value['session_ref'] = session_id
                value['native_session_id'] = session_id
                registry.write_text(json.dumps(value), encoding='utf-8')
                registry.chmod(0o600)

            def codex(**kwargs):
                return {'thread_id': '0123456789abcdef', 'verdict': 'PASS',
                        'review_text': body}

            def dispatch_github(path):
                self.assertEqual(path, 'pulls/10')
                return pr

            if skip_completed_claim:
                reviewer_task = 'reviewer-without-claim'
                create_d6(task_id=reviewer_task, reviewer_key='codex-reviewer',
                          base_sha=head, github_login='reviewer-user')
                bind_d6(reviewer_task, 'codex-reviewer-session', None)
            else:
                with patch.object(review_dispatch, '_resolve_github_login',
                                  return_value='reviewer-user'), \
                        patch.object(review_dispatch, '_create_d6', side_effect=create_d6), \
                        patch.object(review_dispatch, '_bind_d6', side_effect=bind_d6), \
                        patch.object(review_dispatch, '_dispatch_codex', side_effect=codex), \
                        patch('llmtool.task_completion._github_get',
                              side_effect=dispatch_github):
                    rc, dispatched = call(['task', 'review-dispatch', task_id,
                        '--executor-task', d6_task, '--pr', '10',
                        '--reviewer', 'codex-reviewer'])
                self.assertIsNone(rc)
                reviewer_task = dispatched['reviewer_task']

            checks = {'total_count': 2, 'check_runs': [
                {'id': 1, 'name': 'check (macOS 3.13)', 'head_sha': head,
                 'status': 'completed', 'conclusion': 'success'},
                {'id': 2, 'name': 'check (Linux 3.14)', 'head_sha': head,
                 'status': 'completed', 'conclusion': 'success'},
            ]}
            comment = {'id': 44, 'user': {'login': 'reviewer-user'}, 'body': body,
                'created_at': '2026-11-05T00:00:00Z',
                'updated_at': '2026-11-05T00:00:00Z'}
            github_responses = [
                {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': base}},
                *self._github_response_sequence(head, pr, [], checks,
                                                comments=[comment]),
            ]

            def remote_test_at(tested_head, github_get):
                tested = dict(receipt, source_sha=tested_head, fetch_head=tested_head,
                              tree=self._git('rev-parse', tested_head + '^{tree}'))
                ssh_calls = []

                def ssh(argv, script, timeout):
                    ssh_calls.append(argv)
                    return subprocess.CompletedProcess(
                        argv, 0, json.dumps(tested).encode(), b'')

                with patch.dict(os.environ, {
                        'HOME': str(home), 'PATH': '/usr/bin:/bin',
                        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
                        clear=True), \
                        patch.object(remote_test, '_github_get', side_effect=github_get), \
                        patch('llmtool.task_completion._github_get',
                              side_effect=github_get):
                    result = real_remote_test(
                        task_id, executor_task=d6_task, sha=tested_head,
                        ref='refs/heads/feat/executor', modules=('test_fixture',), pr=10,
                        store=store, config_path=remote_config, transport=ssh)
                return result, ssh_calls

            def review_at(version, review_text, github_get):
                prompts = []

                def reviewer_run(**kwargs):
                    prompts.append(kwargs['prompt'])
                    return {'thread_id': 'fedcba9876543210', 'verdict': 'PASS',
                            'review_text': review_text}

                with patch.object(review_dispatch, '_resolve_github_login',
                                  return_value='reviewer-user'), \
                        patch.object(review_dispatch, '_create_d6', side_effect=create_d6), \
                        patch.object(review_dispatch, '_bind_d6', side_effect=bind_d6), \
                        patch.object(review_dispatch, '_dispatch_codex',
                                     side_effect=reviewer_run), \
                        patch('llmtool.task_completion._github_get',
                              side_effect=github_get):
                    rc, result = call(['task', 'review-dispatch', task_id,
                        '--executor-task', d6_task, '--pr', '10',
                        '--reviewer', 'codex-reviewer', '--version', str(version)])
                return rc, result, prompts

            return {
                'store': store, 'call': call, 'task_id': task_id, 'head': head,
                'reviewer_task': reviewer_task, 'pr': pr, 'run_id': run_id,
                'contract_digest': contract_digest, 'remote_test_at': remote_test_at,
                'review_at': review_at,
                'completion_argv': ['task', 'complete', task_id,
                    '--executor-task', d6_task, '--reviewer-task', reviewer_task,
                    '--pr', '10'],
                'github_responses': github_responses, 'body': body,
                'root': root, 'common': self.common, 'base': base,
            }

    def _complete_claude_bound_context(self, context):
        with patch('llmtool.task_completion._github_get',
                   side_effect=context['github_responses']):
            return context['call'](context['completion_argv'])

    def test_claude_bound_task_cli_end_to_end(self):
        context = self._claude_bound_task_context()
        rc, completed = self._complete_claude_bound_context(context)
        self.assertEqual(rc, 0)
        completion = completed['completion']
        self.assertEqual(completion['state'], 'PM_EVIDENCE_READY')
        self.assertEqual(completion['missing_gates'], [])
        self.assertEqual(completion['evidence']['result']['result_head'], context['head'])
        review_ci = completion['evidence']['review_pr_ci']
        self.assertEqual(review_ci['formal_review_state'], 'ACCEPTED')
        self.assertEqual(review_ci['ci_head_sha'], context['head'])
        self.assertEqual(review_ci['required_checks'], [
            'check (Linux 3.14)', 'check (macOS 3.13)'])
        self.assertEqual(completion['pm_dispatch'], 'NOT_STARTED')
        self.assertIsNone(completed['developer']['artifact'])
        saved = context['store'].get(context['task_id'])
        self.assertEqual(saved['remote_test_receipts'][-1]['result'], 'PASS')
        self.assertEqual(saved['remote_test_receipts'][-1]['source_sha'], context['head'])
        claims = [claim for claim in saved['review_dispatch_claim']
                  if claim['state'] == 'COMPLETED']
        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.assertEqual((claim['pr'], claim['head_sha'], claim['reviewer_task']),
                         (10, context['head'], context['reviewer_task']))
        self.assertEqual(claim['review_result']['review_text'], context['body'])

    def _main_merged_context(self):
        """Complete a Claude Task at H1, then merge a newer main into its branch."""
        context = self._claude_bound_task_context()
        rc, first = self._complete_claude_bound_context(context)
        self.assertEqual(rc, 0)
        self.assertEqual(first['completion']['evidence']['result']['result_head'],
                         context['head'])
        trusted = self._move_main('README.md', 'Main moved.\n')
        return context, self._merge_main(), trusted

    def _pr_and_main_reads(self, context, head, main_sha):
        def github_get(path):
            if path == 'git/ref/heads/main':
                return {'ref': 'refs/heads/main',
                        'object': {'type': 'commit', 'sha': main_sha}}
            self.assertEqual(path, 'pulls/10')
            return dict(context['pr'], head={'sha': head})

        return github_get

    def _bound_pass(self, context, head, version):
        return ('<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n'
                f'Verdict: PASS | Version: v{version} | HEAD: {head}\n'
                'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
                f'LLMTOOL-REVIEW-BINDING v1 task={context["task_id"]} '
                f'run={context["run_id"]} contract={context["contract_digest"]} head={head}')

    def _complete_at(self, context, head, main_sha, *, reviewer_task, body):
        checks = {'total_count': 2, 'check_runs': [
            {'id': 1, 'name': 'check (macOS 3.13)', 'head_sha': head,
             'status': 'completed', 'conclusion': 'success'},
            {'id': 2, 'name': 'check (Linux 3.14)', 'head_sha': head,
             'status': 'completed', 'conclusion': 'success'},
        ]}
        comment = {'id': 45, 'user': {'login': 'reviewer-user'}, 'body': body,
                   'created_at': '2026-11-06T00:00:00Z',
                   'updated_at': '2026-11-06T00:00:00Z'}
        responses = [
            {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': main_sha}},
            *self._github_response_sequence(
                head, dict(context['pr'], head={'sha': head}), [], checks,
                comments=[comment]),
        ]
        with patch('llmtool.task_completion._github_get', side_effect=responses):
            return context['call'](['task', 'complete', context['task_id'],
                                    '--executor-task', '4' * 32,
                                    '--reviewer-task', reviewer_task, '--pr', '10'])

    def test_clean_main_merge_head_is_reviewed_tested_and_completed_at_the_new_head(self):
        context, head, trusted = self._main_merged_context()
        store, task_id, first_head = context['store'], context['task_id'], context['head']
        before = store.get(task_id)
        reads = self._pr_and_main_reads(context, head, trusted)

        remote, ssh_calls = context['remote_test_at'](head, reads)
        self.assertEqual(remote['result'], 'PASS')
        self.assertEqual(len(ssh_calls), 1)

        body = self._bound_pass(context, head, 2)
        rc, dispatched, prompts = context['review_at'](2, body, reads)
        self.assertIsNone(rc)
        self.assertEqual(len(prompts), 1)
        self.assertIn(f'Review PR #10 at exact HEAD {head}.', prompts[0])
        self.assertIn(f'Verdict: PASS | Version: v2 | HEAD: {head}', prompts[0])
        self.assertIn(f'contract={context["contract_digest"]} head={head}', prompts[0])
        self.assertNotIn(first_head, prompts[0])
        reviewer_task = dispatched['reviewer_task']
        self.assertNotEqual(reviewer_task, context['reviewer_task'])

        # The H1 PASS and its Reviewer are not evidence for H2.
        rc, stale = self._complete_at(context, head, trusted,
                                      reviewer_task=context['reviewer_task'],
                                      body=context['body'])
        self.assertEqual(rc, 1)
        self.assertEqual(stale['completion']['state'], 'WAITING_REVIEW')
        self.assertIn('FORMAL_REVIEW_MISSING', stale['completion']['missing_gates'])
        self.assertIn('REVIEWER_D6_HEAD_MISMATCH', stale['completion']['missing_gates'])
        self.assertEqual(stale['completion']['pm_dispatch'], 'NOT_STARTED')

        # Main moves again after the pin: the attempt reads it afresh.
        later = self._move_main('main-two.txt', 'Main moved after the pin.\n')
        rc, completed = self._complete_at(context, head, later,
                                          reviewer_task=reviewer_task, body=body)
        self.assertEqual(rc, 0)
        completion = completed['completion']
        self.assertEqual(completion['state'], 'PM_EVIDENCE_READY')
        self.assertEqual(completion['missing_gates'], [])
        saved = store.get(task_id)
        evidence = saved['completion']['evidence']
        self.assertEqual(evidence['result']['result_head'], head)
        self.assertEqual(evidence['result']['developer_changed_paths'], [self.source_path])
        self.assertIn('README.md', evidence['result']['changed_paths'])
        review_ci = evidence['review_pr_ci']
        self.assertEqual(review_ci['formal_review_state'], 'ACCEPTED')
        self.assertEqual(review_ci['reviewer_task_id'], reviewer_task)
        self.assertEqual(review_ci['review_task_id'], task_id)
        self.assertIs(review_ci['review_binding_verified'], True)
        self.assertEqual(review_ci['ci_head_sha'], head)
        pin = evidence['trusted_main_pin']
        self.assertEqual(pin['sha'], later)
        self.assertEqual(pin['verified_result_heads'], [first_head, head])

        self.assertEqual(saved['state'], before['state'])
        self.assertEqual(saved['run_id'], before['run_id'])
        self.assertEqual(saved['task_binding'], before['task_binding'])
        self.assertEqual(saved['developer'], before['developer'])
        self.assertEqual(
            [(claim['head_sha'], claim['version'], claim['state'])
             for claim in saved['review_dispatch_claim']],
            [(first_head, 1, 'COMPLETED'), (head, 2, 'COMPLETED')])
        self.assertEqual([receipt['source_sha'] for receipt in saved['remote_test_receipts']],
                         [first_head, head])

    def test_new_developer_commit_after_a_main_merge_is_refused_before_side_effects(self):
        from llmtool import review_dispatch

        context, _, trusted = self._main_merged_context()
        store, task_id = context['store'], context['task_id']
        head = self._register_head(self._developer_commit(text='# later change\n'))
        before = store.get(task_id)
        reads = self._pr_and_main_reads(context, head, trusted)

        with patch('llmtool.task_completion._github_get', side_effect=reads), \
                self.assertRaises(LLMToolError) as dispatch_refused:
            review_dispatch.dispatch_review(
                store=store, task_id=task_id, executor_task='4' * 32, pr_number=10,
                version=2, reviewer='codex-reviewer',
                create_d6=lambda **_kw: self.fail('Reviewer D6 created'))
        self.assertEqual(dispatch_refused.exception.review_dispatch_code,
                         'REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')

        output = StringIO()
        args = Namespace(task_id=task_id, executor_task='4' * 32,
                         reviewer_task='reviewer-none', pr=10, version=2)
        with patch('llmtool.task_completion._github_get', side_effect=reads), \
                redirect_stdout(output), self.assertRaises(LLMToolError) as prompt_refused:
            run_task_review_prompt(args, store=store)
        self.assertEqual(prompt_refused.exception.completion_code,
                         'REVIEW_PROMPT_D6_UNAVAILABLE')
        self.assertEqual(output.getvalue(), '')

        remote, ssh_calls = context['remote_test_at'](head, reads)
        self.assertEqual((remote['result'], remote['code']),
                         ('ADMISSION_DENIED', 'RECEIPT_HEAD_MISMATCH'))
        self.assertEqual(ssh_calls, [])

        saved = store.get(task_id)
        self.assertEqual(saved['review_dispatch_claim'], before['review_dispatch_claim'])
        self.assertEqual(saved['remote_test_receipts'], before['remote_test_receipts'])
        self.assertEqual(saved['completion']['evidence']['result']['result_head'],
                         context['head'])

    def test_failed_main_read_on_retry_stays_the_pin_gate(self):
        context, _, _ = self._main_merged_context()
        with patch('llmtool.task_completion._github_get',
                   side_effect=LLMToolError('unavailable')):
            rc, result = context['call'](context['completion_argv'])
        self.assertEqual(rc, 1)
        completion = result['completion']
        self.assertEqual(completion['missing_gates'], ['MAIN_REF_PIN_INVALID'])
        self.assertEqual(completion['state'], 'WAITING_EXECUTOR')
        saved = context['store'].get(context['task_id'])
        pin = saved['completion']['evidence']['trusted_main_pin']
        self.assertEqual(pin['sha'], context['base'])
        self.assertEqual(pin['verified_result_heads'], [context['head']])

    def test_claude_completion_racing_with_abandon_does_not_persist_ready(self):
        context = self._claude_bound_task_context()
        store = context['store']
        collect = task_completion.collect_claude_result

        def collect_then_abandon(*args, **kwargs):
            result = collect(*args, **kwargs)
            current = store.get(context['task_id'])
            store.abandon(context['task_id'],
                          pm_session_id=current['pm_session_id'],
                          reason='raced completion')
            return result

        with patch.object(task_completion, 'collect_claude_result',
                          side_effect=collect_then_abandon), \
                patch('llmtool.task_completion._github_get',
                      side_effect=context['github_responses']):
            rc, _result = context['call'](context['completion_argv'])
        self.assertNotEqual(rc, 0)
        saved = store.get(context['task_id'])
        self.assertIsNotNone(saved.get('abandonment'))
        self.assertNotEqual((saved.get('completion') or {}).get('state'),
                            'PM_EVIDENCE_READY')

    def test_abandoned_claude_task_is_refused_by_completion_review_and_remote_test(self):
        from llmtool import remote_test, review_dispatch

        context = self._claude_bound_task_context()
        store = context['store']
        task = store.get(context['task_id'])
        store.abandon(context['task_id'], pm_session_id=task['pm_session_id'],
                      reason='superseded')
        rc, result = self._complete_claude_bound_context(context)
        self.assertNotEqual(rc, 0)
        saved = store.get(context['task_id'])
        self.assertNotEqual((saved.get('completion') or {}).get('state'),
                            'PM_EVIDENCE_READY')

        with self.assertRaises(LLMToolError) as review_error:
            review_dispatch._preflight(store, context['task_id'], '4' * 32, 10, 'v1')
        self.assertEqual(review_error.exception.review_dispatch_code,
                         'CLAUDE_TASK_ABANDONED')

        ssh_calls = []
        result = remote_test.run_remote_test(
            context['task_id'], executor_task='4' * 32, sha=context['head'],
            ref='refs/heads/feat/executor', modules=('test_fixture',), store=store,
            config_path=Path(self.base) / 'missing-remote-config',
            transport=lambda *args: ssh_calls.append(args))
        self.assertEqual((result['result'], result['code']),
                         ('ADMISSION_DENIED', 'CLAUDE_TASK_ABANDONED'))
        self.assertEqual(ssh_calls, [])

    def test_abandonment_does_not_apply_to_non_claude_task(self):
        from types import SimpleNamespace
        from llmtool import review_dispatch

        context = self._claude_bound_task_context()
        store = context['store']
        task = store.get(context['task_id'])
        task = dict(task)
        developer = dict(task['developer'])
        developer['provider_profile'] = 'opencode'
        task['developer'] = developer
        task['abandonment'] = {'reason': 'superseded'}
        store = SimpleNamespace(get=lambda _task_id: dict(task))
        with patch('llmtool.task_completion._github_get',
                   side_effect=LLMToolError('offline')):
            with self.assertRaises(LLMToolError) as review_error:
                review_dispatch._preflight(store, context['task_id'], '4' * 32, 10, 'v1')
        self.assertNotEqual(getattr(review_error.exception, 'review_dispatch_code', None),
                            'CLAUDE_TASK_ABANDONED')

    def _assert_cli_completion_waits(self, context, expected_gate, expected_state):
        rc, result = self._complete_claude_bound_context(context)
        self.assertEqual(rc, 1)
        completion = result['completion']
        self.assertEqual(completion['missing_gates'], [expected_gate])
        self.assertEqual(completion['state'], expected_state)
        self.assertEqual(completion['pm_dispatch'], 'NOT_STARTED')

    def test_claude_bound_complete_rejects_wrong_claude_result_head(self):
        context = self._claude_bound_task_context()
        task = context['store'].get(context['task_id'])
        developer = dict(task['developer'])
        claude_result = dict(developer['claude_result'])
        claude_result['head_sha'] = context['base']
        developer['claude_result'] = claude_result
        context['store'].update(context['task_id'], developer=developer)
        self._assert_cli_completion_waits(
            context, 'CLAUDE_RESULT_BINDING_INVALID', 'WAITING_EXECUTOR')

    def test_claude_bound_complete_rejects_stale_env2_marker(self):
        context = self._claude_bound_task_context()
        registry = Path(context['common']) / 'llmtool-worktrees' / ('4' * 32 + '.json')
        value = json.loads(registry.read_text(encoding='utf-8'))
        value['env2_pass_marker']['current_head_sha'] = context['base']
        registry.write_text(json.dumps(value), encoding='utf-8')
        registry.chmod(0o600)
        self._assert_cli_completion_waits(
            context, 'D6_ENV2_PASS_MARKER_MISMATCH', 'WAITING_EXECUTOR')

    def test_claude_bound_complete_requires_remote_test_receipt(self):
        context = self._claude_bound_task_context(skip_remote_test=True)
        self._assert_cli_completion_waits(
            context, 'REMOTE_TEST_RECEIPT_MISSING', 'WAITING_EXECUTOR')

    def test_claude_bound_complete_rejects_old_sha_remote_receipt(self):
        context = self._claude_bound_task_context(old_sha_receipt=True)
        self._assert_cli_completion_waits(
            context, 'REMOTE_TEST_RECEIPT_MISSING', 'WAITING_EXECUTOR')

    def test_claude_bound_complete_waits_on_out_of_scope_first_parent_merge(self):
        original_make_result = self._make_result
        calls = 0

        def make_result_with_out_of_scope_head(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                result, _, _ = self._out_of_scope_merge_head()
                patch_text = subprocess.check_output(
                    ['/usr/bin/git', 'diff', '--binary', self.source_sha, result],
                    cwd=self.root, text=True)
                return result, patch_text
            return original_make_result(**kwargs)

        with patch.object(self, '_make_result', side_effect=make_result_with_out_of_scope_head):
            context = self._claude_bound_task_context(old_sha_receipt=True)
        self._assert_cli_completion_waits(
            context, 'SOURCE_NOT_ANCESTOR', 'WAITING_EXECUTOR')

    def test_claude_bound_complete_waits_on_renamed_out_of_scope_deletion(self):
        original_make_result = self._make_result
        calls = 0

        def make_result_with_renamed_deletion(**kwargs):
            nonlocal calls
            calls += 1
            if calls != 2:
                return original_make_result(**kwargs)
            old = self._git('rev-parse', self.source_sha + '^')
            target = self.root / self.source_path
            guard = self.root / 'guard.txt'
            guard_content = ''.join(f'guard line {index}\n' for index in range(30))
            target.unlink()
            self._git('add', self.source_path)
            self._git('commit', '-m', 'delete source path')
            deletion = self._git('rev-parse', 'HEAD')
            guard.unlink()
            target.write_text(guard_content)
            self._git('add', 'guard.txt', self.source_path)
            self._git('commit', '-m', 'move guard to source path')
            tree = self._git('rev-parse', 'HEAD^{tree}')
            merge = self._commit_tree(tree, deletion, old,
                                      message='merge old ancestor with renamed deletion')
            self._git('reset', '--hard', merge)
            target.write_text('import unittest\nclass Fixture(unittest.TestCase):\n'
                              '    def test_ok(self): self.assertTrue(True)\n')
            self._git('add', self.source_path)
            self._git('commit', '-m', 'restore source content')
            result = self._git('rev-parse', 'HEAD')
            artifact = subprocess.check_output(
                ['/usr/bin/git', 'diff', '--binary', self.source_sha, result],
                cwd=self.root, text=True)
            registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
            record = json.loads(registry_path.read_text())
            record['head_sha'] = result
            self._write_env2_marker(record, self.root, submitted_head=result,
                                    current_head=result)
            registry_path.write_text(json.dumps(record))
            registry_path.chmod(0o600)
            return result, artifact

        with patch.object(self, '_make_result', side_effect=make_result_with_renamed_deletion):
            context = self._claude_bound_task_context(
                old_sha_receipt=True, guard_file=True)
        self._assert_cli_completion_waits(
            context, 'RESULT_SCOPE_MISMATCH', 'WAITING_EXECUTOR')

    def test_claude_bound_complete_requires_completed_review_dispatch_claim(self):
        context = self._claude_bound_task_context(skip_completed_claim=True)
        self._assert_cli_completion_waits(
            context, 'FORMAL_REVIEW_MISSING', 'WAITING_REVIEW')

    def test_non_claude_executor_requires_bound_artifact(self):
        result_sha, _ = self._make_result()
        artifact = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', self.source_sha, result_sha],
            cwd=self.root, text=True)
        with self.assertRaises(LLMToolError) as caught:
            task_completion.collect_executor_result(
                task_id=self.task_id, expected_common_dir=self.common,
                expected_root=str(self.root), expected_branch='feat/executor',
                source_sha=self.source_sha,
                task_binding={'task_id': '1' * 32, 'run_id': '2' * 32,
                              'contract_digest': '3' * 64},
                expected_agent='executor-agent', expected_session_ref='Executor D6 session',
                expected_native_session_id='native-executor',
                write_paths=[self.source_path], artifact=None, artifact_sha256=None)
        self.assertEqual(caught.exception.completion_code,
                         'DEVELOPER_ARTIFACT_BINDING_INVALID')


class CompletionAttributedPathsStoreTests(unittest.TestCase):
    """#1169: task complete saves records whose attributed paths leave the net diff.

    Developer attribution (#1177) can name paths outside changed_paths: a
    rename-conflict source or a file added and later deleted. The store must
    save these completion records. The fixture helpers are reused from
    TaskCompletionTests without inheriting (and so re-running) its tests.
    """

    setUp = TaskCompletionTests.setUp
    _git = TaskCompletionTests._git
    _write_registration = TaskCompletionTests._write_registration
    _write_env2_marker = TaskCompletionTests._write_env2_marker
    _trusted_main_ref = TaskCompletionTests._trusted_main_ref
    _make_path_result = TaskCompletionTests._make_path_result
    _segment_head = TaskCompletionTests._segment_head
    _RENAME_CONFLICT_SHAPES = TaskCompletionTests._RENAME_CONFLICT_SHAPES
    _complete_company_gw_task = TaskCompletionTests._complete_company_gw_task
    _active_pm_task = TaskCompletionTests._active_pm_task
    _task = TaskCompletionTests._task

    DOC_PATH = 'docs/guide.md'

    def _markdown_rename_conflict_head(self, shape):
        """Same rename conflict as _rename_conflict_head, with only Markdown changes.

        The Developer branch changes only README.md before the segment, and
        the segment's own commit adds a Markdown guide, so every attributed
        path ends in .md.
        """
        main_name, developer_name, dropped, hidden = self._RENAME_CONFLICT_SHAPES[shape]
        original = 'README.md'
        self._git('checkout', '-f', 'feat/executor')
        self._git('reset', '--hard', self.source_sha)
        self._write_registration(self.task_id, 'executor-agent', 'Executor D6 session',
                                 self.root, 'feat/executor', self.source_sha, self.source_sha,
                                 native_session_id='native-executor')
        main_branch = 'docs-main-' + shape
        self._git('checkout', '-b', main_branch)
        if main_name is None:
            self._git('rm', original)
        else:
            self._git('mv', original, main_name)
        self._git('commit', '-m', 'trusted main moves README')
        trusted_base = self._git('rev-parse', 'HEAD')

        self._git('checkout', 'feat/executor')
        if developer_name is None:
            self._git('rm', original)
        else:
            self._git('mv', original, developer_name)
        self._git('commit', '-m', 'developer moves README')
        segment_start = self._git('rev-parse', 'HEAD')

        merge = subprocess.run(
            ['/usr/bin/git', '-c', 'merge.renames=true', '-c', 'diff.renames=true',
             'merge', '--no-ff', main_branch, '-m', 'merge trusted main'],
            cwd=self.root, text=True, capture_output=True)
        self.assertNotEqual(merge.returncode, 0, merge.stdout + merge.stderr)
        stages = self._git('ls-files', '-u')
        conflicts = {line.split('\t', 1)[1] for line in stages.splitlines()}
        self.assertLessEqual(set(hidden), conflicts, stages)
        if dropped is not None:
            (self.root / dropped).unlink()
        self._git('add', '-A')
        self._git('commit', '-m', 'resolve ' + shape)
        merge_sha = self._git('rev-parse', 'HEAD')
        self.assertEqual(self._git('ls-files', '-u'), '')
        self.assertEqual(self._git('rev-parse', merge_sha + '^1'), segment_start)
        self.assertEqual(self._git('rev-parse', merge_sha + '^2'), trusted_base)
        result_sha = self._make_path_result(self.DOC_PATH, 'Developer guide.\n')
        attributed = conflicts | {self.DOC_PATH}
        self.assertTrue(all(path.endswith('.md') for path in attributed), attributed)
        return self._segment_head(result_sha, trusted_base, segment_start, attributed)

    def _register_head(self, result_sha):
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record['head_sha'] = result_sha
        self._write_env2_marker(record, self.root, submitted_head=result_sha,
                                current_head=result_sha)
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)

    def _bound_task(self, head):
        task = self._active_pm_task(self._task(), route='company-gw')
        paths = list(head['attributed'])
        task['developer'].update(
            source_sha=head['segment_start'], read_paths=paths, write_paths=paths,
            artifact=head['artifact'], artifact_sha256=head['artifact_sha256'])
        task['completion']['evidence'].update(
            source_sha=head['segment_start'], result_head=head['segment_start'],
            executor_native_session_id='native-executor',
            executor_native_session='VERIFIED')
        return task

    def _complete_with_real_tests(self, store, task, *, trusted_base):
        """Run task complete with the real collector and the real docs-only gate."""
        real_collector = task_completion.collect_executor_result
        real_tests = task_completion.run_bounded_tests

        def github_get(path):
            self.assertEqual(path, 'git/ref/heads/main')
            return {'ref': 'refs/heads/main',
                    'object': {'type': 'commit', 'sha': trusted_base}}

        with patch.object(task_completion, '_github_get', side_effect=github_get), \
             patch.object(task_completion, 'collect_executor_result',
                          wraps=real_collector) as collect, \
             patch.object(task_completion, 'run_bounded_tests',
                          wraps=real_tests) as tests, \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   side_effect=AssertionError('unexpected provider')) as resolve, \
             patch.object(task_completion, 'collect_github_evidence',
                          side_effect=AssertionError('unexpected review/CI')) as github, \
             redirect_stdout(StringIO()):
            code = run_task_complete(Namespace(
                task_id=task['task_id'], executor_task=self.task_id,
                reviewer_task=None, pr=None), store=store)
        collect.assert_called_once()
        tests.assert_called_once()
        self.assertEqual(tests.call_args.kwargs['trusted_base_sha'], trusted_base)
        resolve.assert_not_called()
        github.assert_not_called()
        return code, store.get(task['task_id'])['completion']

    def test_markdown_rename_conflict_docs_only_completion_is_saved(self):
        outside_net_diff = {}
        for shape in self._RENAME_CONFLICT_SHAPES:
            with self.subTest(shape=shape):
                head = self._markdown_rename_conflict_head(shape)
                store = TaskStore(self.base / ('docs-rename-conflict-' + shape))
                task = self._bound_task(head)
                store.create(task)
                code, completion = self._complete_with_real_tests(
                    store, task, trusted_base=head['trusted_base'])
                self.assertEqual(code, 0)
                self.assertEqual(completion['state'], 'PM_EVIDENCE_READY')
                self.assertEqual(completion['missing_gates'], [])
                self.assertEqual(completion['pm_dispatch'], 'NOT_STARTED')
                result = completion['evidence']['result']
                self.assertEqual(result['source_sha'], head['segment_start'])
                self.assertEqual(result['result_head'], head['result_sha'])
                self.assertEqual(result['developer_changed_paths'], head['attributed'])
                self.assertEqual(completion['evidence']['tests'], [{
                    'status': 'NOT_APPLICABLE_DOCS_ONLY',
                    'changed_paths': head['attributed'],
                    'source_sha': head['result_sha'], 'docs_only': True}])
                outside_net_diff[shape] = (set(result['developer_changed_paths']) -
                                           set(result['changed_paths']))
        # README.md is renamed away on both sides, so it is attributed but is
        # not in the net diff: the record the old subset rule refused.
        self.assertIn('README.md', outside_net_diff['rename-rename-keep-both'])

    def test_added_then_deleted_path_is_attributed_and_completion_is_saved(self):
        scratch = 'docs/scratch.md'
        self._git('checkout', '-f', 'feat/executor')
        self._git('reset', '--hard', self.source_sha)
        source = self.root / self.source_path
        source.write_text(source.read_text() + '\n# scoped developer change\n')
        (self.root / 'docs').mkdir(exist_ok=True)
        (self.root / scratch).write_text('Temporary Developer notes.\n')
        self._git('add', self.source_path, scratch)
        self._git('commit', '-m', 'developer change with scratch notes')
        self._git('rm', scratch)
        self._git('commit', '-m', 'developer removes scratch notes')
        result_sha = self._git('rev-parse', 'HEAD')
        self._register_head(result_sha)
        head = self._segment_head(result_sha, self.source_sha, self.source_sha,
                                  {self.source_path, scratch})

        store = TaskStore(self.base / 'added-then-deleted')
        task = self._bound_task(head)
        store.create(task)
        code, tests, collect, completion = self._complete_company_gw_task(
            store, task, trusted_base=self.source_sha, tested_head=result_sha)
        self.assertEqual(code, 0)
        tests.assert_called_once()
        self.assertEqual(completion['state'], 'PM_EVIDENCE_READY')
        self.assertEqual(completion['missing_gates'], [])
        result = completion['evidence']['result']
        self.assertEqual(result['result_head'], result_sha)
        self.assertEqual(result['changed_paths'], [self.source_path])
        self.assertEqual(result['developer_changed_paths'],
                         sorted([self.source_path, scratch]))
        self.assertNotIn(scratch, result['changed_paths'])


if __name__ == '__main__':
    unittest.main()
