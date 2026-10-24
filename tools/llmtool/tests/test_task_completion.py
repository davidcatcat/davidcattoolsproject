import hashlib
import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool.providers.base import Reply
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import _record, run_task_complete
from llmtool import task_completion
from llmtool import worker_dispatch


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
        self._git('add', self.source_path, 'tools/llmtool/.gitignore')
        self._git('commit', '-m', 'source')
        self.source_sha = self._git('rev-parse', 'HEAD')
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
                  'reviewer_task_id': 'reviewer-task', 'common_dir': self.common}
        kwargs.update(overrides)
        return kwargs

    def _github_response_sequence(self, result_sha, pr, reviews, checks, *,
                                  workflow=None, runs=None, jobs=None):
        workflow = workflow or {'id': 42, 'path': '.github/workflows/llmtool-ci.yml',
                                'name': 'llmtool CI', 'state': 'active'}
        check_runs = checks['check_runs']
        for check in check_runs:
            check.setdefault('app', {'id': 15368, 'slug': 'github-actions',
                                     'name': 'GitHub Actions'})
        by_name = {check['name']: check for check in check_runs}
        jobs = jobs or {'total_count': 2, 'jobs': [
            {'id': index + 10, 'run_id': 100, 'name': name,
             'check_run_url': ('https://api.github.com/repos/davidcatcat/davidcattoolsproject/'
                               'check-runs/' + str(by_name.get(name, {}).get('id', 1000 + index))),
             'status': by_name.get(name, {}).get('status', 'completed'),
             'conclusion': by_name.get(name, {}).get('conclusion', 'success')}
            for index, name in enumerate(('check (3.9)', 'check (3.14)'))]}
        runs = runs or {'total_count': 1, 'workflow_runs': [{
            'id': 100, 'run_number': 5, 'run_attempt': 1, 'workflow_id': workflow['id'],
            'path': '.github/workflows/llmtool-ci.yml', 'event': 'pull_request',
            'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success',
            'pull_requests': [{'number': 10, 'head': {'sha': result_sha}}]}]}
        return [pr, reviews, workflow, runs, jobs, checks]

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
            root=str(self.root), result_sha=result_sha, read_paths=[self.source_path],
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
            root=str(self.root), result_sha=result_sha, read_paths=[self.source_path],
            write_paths=[self.source_path])
        self.assertNotEqual(evidence[0]['exit_code'], 0)

    def test_github_review_requires_d6_actor_session_and_live_exact_head(self):
        result_sha, _ = self._make_result()
        reviewer_root = self.base / 'reviewer'
        self._git('worktree', 'add', '-b', 'review/branch', str(reviewer_root), result_sha)
        reviewer_head = result_sha
        self._write_registration('reviewer-task', 'review-agent', 'Reviewer session',
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
        checks = {'total_count': 2, 'check_runs': [
            {'id': i, 'name': name, 'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success'}
            for i, name in enumerate(('check (3.9)', 'check (3.14)'), 1)]}
        api = self._github_response_sequence(result_sha, pr, review, checks)
        with patch('llmtool.task_completion._github_get', side_effect=api):
            evidence = task_completion.collect_github_evidence(**self._github_kwargs(result_sha))
        self.assertEqual(evidence['evidence']['formal_review_state'], 'ACCEPTED')
        self.assertEqual(evidence['evidence']['review_task_id'], '1' * 32)
        self.assertTrue(evidence['evidence']['review_binding_verified'])
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
        checks = {'total_count': 2, 'check_runs': [
            {'id': i, 'name': name, 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success'}
            for i, name in enumerate(('check (3.9)', 'check (3.14)'), 1)]}
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
        checks = {'total_count': 2, 'check_runs': [
            {'id': i, 'name': name, 'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success'}
            for i, name in enumerate(('check (3.9)', 'check (3.14)'), 1)]}
        with patch('llmtool.task_completion._github_get', side_effect=self._github_response_sequence(
                result_sha, pr, review, checks)):
            result = task_completion.collect_github_evidence(**self._github_kwargs(
                result_sha, executor_native_session_id=None))
        self.assertEqual(result['evidence']['formal_review_state'], 'UNVERIFIED')
        self.assertEqual(result['evidence']['review_session_identity'], 'UNKNOWN')
        self.assertIn('REVIEWER_NATIVE_SESSION_UNVERIFIED', result['missing_gates'])

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
            {'id': 1, 'name': 'check (3.9)', 'head_sha': '8' * 40,
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
            checks = {'total_count': 3, 'check_runs': [
                {'id': 1, 'name': 'check (3.9)', 'head_sha': result_sha,
                 'status': 'completed', 'conclusion': 'success'},
                {'id': 3, 'name': 'check (3.9)', 'head_sha': result_sha,
                 'status': status, 'conclusion': conclusion},
                {'id': 2, 'name': 'check (3.14)', 'head_sha': result_sha,
                 'status': 'completed', 'conclusion': 'success'}]}
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
        checks = {'total_count': 2, 'check_runs': [
            {'id': i, 'name': name, 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success'}
            for i, name in enumerate(('check (3.9)', 'check (3.14)'), 1)]}
        wrong_workflow = {'id': 42, 'path': '.github/workflows/other.yml',
                          'name': 'llmtool CI', 'state': 'active'}
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
        self.assertEqual(evidence['evidence']['successful_checks'], [])

    def test_ci_selects_latest_exact_pull_request_workflow_run_and_attempt(self):
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
             'path': '.github/workflows/llmtool-ci.yml', 'event': 'pull_request',
             'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success',
             'pull_requests': [{'number': 10, 'head': {'sha': result_sha}}]},
            {'id': 101, 'run_number': 5, 'run_attempt': 2, 'workflow_id': 42,
             'path': '.github/workflows/llmtool-ci.yml', 'event': 'pull_request',
             'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success',
             'pull_requests': [{'number': 10, 'head': {'sha': result_sha}}]}]}
        jobs = {'total_count': 2, 'jobs': [
            {'id': 11 + index, 'run_id': 101, 'name': name,
             'check_run_url': ('https://api.github.com/repos/davidcatcat/davidcattoolsproject/'
                               f'check-runs/{21 + index}'),
             'status': 'completed', 'conclusion': 'success'}
            for index, name in enumerate(('check (3.9)', 'check (3.14)'))]}
        checks = {'total_count': 2, 'check_runs': [
            {'id': 21 + index, 'name': name, 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'success',
             'app': {'id': 15368, 'slug': 'github-actions', 'name': 'GitHub Actions'}}
            for index, name in enumerate(('check (3.9)', 'check (3.14)'))]}
        api = self._github_response_sequence(result_sha, pr, reviews, checks,
                                             runs=runs, jobs=jobs)
        with patch('llmtool.task_completion._github_get', side_effect=api) as github_get:
            evidence = task_completion.collect_github_evidence(**self._github_kwargs(result_sha))
        self.assertEqual(evidence['evidence']['ci_run_id'], 101)
        self.assertEqual(evidence['evidence']['ci_run_attempt'], 2)
        self.assertEqual(evidence['evidence']['ci_check_ids'],
                         {'check (3.9)': 21, 'check (3.14)': 22})
        self.assertEqual(evidence['missing_gates'], [])
        self.assertIn('actions/runs/101/attempts/2/jobs?per_page=100',
                      github_get.call_args_list[4].args[0])

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
             'path': '.github/workflows/llmtool-ci.yml', 'event': 'pull_request',
             'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success',
             'pull_requests': [{'number': 10, 'head': {'sha': result_sha}}]},
            {'id': 101, 'run_number': 5, 'run_attempt': 1, 'workflow_id': 42,
             'path': '.github/workflows/llmtool-ci.yml', 'event': 'pull_request',
             'head_sha': result_sha, 'status': 'completed', 'conclusion': 'failure',
             'pull_requests': [{'number': 10, 'head': {'sha': result_sha}}]}]}
        jobs = {'total_count': 2, 'jobs': [
            {'id': 11 + index, 'run_id': 101, 'name': name,
             'check_run_url': ('https://api.github.com/repos/davidcatcat/davidcattoolsproject/'
                               f'check-runs/{21 + index}'),
             'status': 'completed', 'conclusion': 'failure'}
            for index, name in enumerate(('check (3.9)', 'check (3.14)'))]}
        checks = {'total_count': 2, 'check_runs': [
            {'id': 21 + index, 'name': name, 'head_sha': result_sha,
             'status': 'completed', 'conclusion': 'failure',
             'app': {'id': 15368, 'slug': 'github-actions', 'name': 'GitHub Actions'}}
            for index, name in enumerate(('check (3.9)', 'check (3.14)'))]}
        api = self._github_response_sequence(result_sha, pr, reviews, checks,
                                             runs=runs, jobs=jobs)
        with patch('llmtool.task_completion._github_get', side_effect=api):
            evidence = task_completion.collect_github_evidence(**self._github_kwargs(result_sha))
        self.assertEqual(evidence['evidence']['ci_run_id'], 101)
        self.assertIn('REQUIRED_CI_MISSING', evidence['missing_gates'])
        self.assertEqual(evidence['evidence']['successful_checks'], [])

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

    def _direct_opencode_task(self, store_name):
        from llmtool.task_submission_cli import _direct_result_callback

        store = TaskStore(self.base / store_name)
        task = self._task()
        developer = task['developer']
        artifact = 'diff --git a/test_fixture.py b/test_fixture.py\n'
        developer.update(provider_profile='opencode', provider_type='opencode-cli',
            model='opencode/deepseek-v4.1-flash-free',
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
             'required_checks': ['check (3.9)', 'check (3.14)'],
             'successful_checks': ['check (3.9)', 'check (3.14)'], 'ci_head_sha': '7' * 40},
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
                with self.assertRaises(LLMToolError):
                    task_completion.review_repair_intent(other_handoff, other_return)
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


if __name__ == '__main__':
    unittest.main()
