import hashlib
import json
import os
import subprocess
import tempfile
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


class FakeClient:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
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
        self.root = self.base / 'executor'
        self.root.mkdir(mode=0o700)
        self._git('init', '-b', 'feat/executor')
        self._git('config', 'user.name', 'Executor')
        self._git('config', 'user.email', 'executor@example.invalid')
        self.source_path = 'tools/llmtool/tests/test_fixture.py'
        source = self.root / self.source_path
        source.parent.mkdir(parents=True)
        source.write_text('import unittest\nclass Fixture(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n')
        self._git('add', self.source_path)
        self._git('commit', '-m', 'source')
        self.source_sha = self._git('rev-parse', 'HEAD')
        self.common = str(self.root / '.git')
        (Path(self.common) / 'llmtool-worktrees').mkdir(mode=0o700)
        self.task_id = 'executor-task'
        self._write_registration(self.task_id, 'executor-agent', 'Executor D6 session',
                                 self.root, 'feat/executor', self.source_sha, self.source_sha,
                                 native_session_id='native-executor')

    def _git(self, *args):
        return subprocess.check_output(['/usr/bin/git', *args], cwd=self.root,
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def _write_registration(self, task_id, agent, session, root, branch, base, head,
                            **extra):
        root_stat = root.stat()
        value = {'schema_version': 1, 'task_id': task_id,
                 'trusted_agent_id': agent, 'session_ref': session,
                 'root_realpath': str(root),
                 'physical_identity': {'device': root_stat.st_dev,
                                       'inode': root_stat.st_ino, 'uid': root_stat.st_uid},
                 'git_common_dir': self.common, 'branch': branch, 'base_sha': base,
                 'head_sha': head, 'state': 'ACTIVE', 'lock_state': 'LOCKED'}
        value.update(extra)
        path = Path(self.common) / 'llmtool-worktrees' / (task_id + '.json')
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return path

    def _make_result(self, *, code='    def test_ok(self): self.assertEqual(1, 1)\n'):
        path = self.root / self.source_path
        path.write_text('import unittest\nclass Fixture(unittest.TestCase):\n' + code)
        self._git('add', self.source_path)
        self._git('commit', '-m', 'executor result')
        result_sha = self._git('rev-parse', 'HEAD')
        patch_text = subprocess.check_output(
            ['/usr/bin/git', 'diff', '--binary', self.source_sha, result_sha],
            cwd=self.root, text=True)
        registry_path = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry_path.read_text())
        record['head_sha'] = result_sha
        registry_path.write_text(json.dumps(record))
        registry_path.chmod(0o600)
        return result_sha, patch_text

    def test_actual_registered_diff_and_bounded_test_subprocess_bind_exact_head(self):
        result_sha, artifact = self._make_result()
        binding = {'task_id': '1' * 32, 'run_id': '2' * 32,
                   'contract_digest': '3' * 64}
        result = task_completion.collect_executor_result(
            task_id=self.task_id, expected_common_dir=self.common,
            expected_root=str(self.root), expected_branch='feat/executor',
            source_sha=self.source_sha, task_binding=binding,
            expected_agent='executor-agent', expected_session_ref='Executor D6 session',
            expected_native_session_id='native-executor', write_paths=[self.source_path],
            artifact=artifact, artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        self.assertEqual(result['result_head'], result_sha)
        self.assertEqual(result['changed_paths'], [self.source_path])
        tests = task_completion.run_bounded_tests(
            root=str(self.root), result_sha=result_sha, read_paths=[self.source_path],
            write_paths=[self.source_path])
        self.assertEqual(tests[0]['exit_code'], 0)
        self.assertEqual(tests[0]['source_sha'], result_sha)
        self.assertRegex(tests[0]['output_sha256'], r'^[0-9a-f]{64}$')

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
            native_session_id='native-reviewer', github_login='reviewer-user')
        pr = {'state': 'open', 'merged': False,
              'head': {'sha': result_sha}, 'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        review = [{'state': 'APPROVED', 'commit_id': result_sha,
                   'user': {'login': 'reviewer-user'},
                   'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT'}]
        checks = {'total_count': 2, 'check_runs': [
            {'name': name, 'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success'}
            for name in ('check (3.9)', 'check (3.14)')]}
        with patch('llmtool.task_completion._github_get', side_effect=[pr, review, checks]):
            evidence = task_completion.collect_github_evidence(
                pr_number=10, result_sha=result_sha, executor_identity='executor-agent',
                executor_session_ref='Executor D6 session', executor_native_session_id='native-executor',
                reviewer_task_id='reviewer-task', common_dir=self.common)
        self.assertEqual(evidence['evidence']['formal_review_state'], 'ACCEPTED')
        self.assertEqual(evidence['missing_gates'], [])
        with patch('llmtool.task_completion._github_get', side_effect=[pr, review, checks]):
            unknown_executor_native = task_completion.collect_github_evidence(
                pr_number=10, result_sha=result_sha, executor_identity='executor-agent',
                executor_session_ref='Executor D6 session', executor_native_session_id=None,
                reviewer_task_id='reviewer-task', common_dir=self.common)
        self.assertEqual(unknown_executor_native['missing_gates'], [])
        superseded_review = review + [{'state': 'CHANGES_REQUESTED',
            'commit_id': result_sha, 'user': {'login': 'reviewer-user'},
            'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT'}]
        with patch('llmtool.task_completion._github_get',
                   side_effect=[pr, superseded_review, checks]):
            rejected = task_completion.collect_github_evidence(
                pr_number=10, result_sha=result_sha, executor_identity='executor-agent',
                executor_session_ref='Executor D6 session',
                executor_native_session_id='native-executor',
                reviewer_task_id='reviewer-task', common_dir=self.common)
        self.assertEqual(rejected['evidence']['formal_review_state'], 'UNVERIFIED')
        self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', rejected['missing_gates'])

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
                task_completion.collect_github_evidence(
                    pr_number=10, result_sha=result_sha, executor_identity='executor-agent',
                    executor_session_ref='Executor D6 session',
                    executor_native_session_id='native-executor',
                    reviewer_task_id='reviewer-task', common_dir=self.common)

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
                   'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT'}]
        checks = {'total_count': 2, 'check_runs': [
            {'name': name, 'head_sha': result_sha, 'status': 'completed', 'conclusion': 'success'}
            for name in ('check (3.9)', 'check (3.14)')]}
        with patch('llmtool.task_completion._github_get', side_effect=[pr, review, checks]):
            result = task_completion.collect_github_evidence(
                pr_number=10, result_sha=result_sha, executor_identity='executor-agent',
                executor_session_ref='Executor D6 session', executor_native_session_id=None,
                reviewer_task_id='reviewer-task', common_dir=self.common)
        self.assertEqual(result['evidence']['formal_review_state'], 'ACCEPTED')
        self.assertEqual(result['evidence']['review_session_identity'], 'UNKNOWN')
        self.assertEqual(result['missing_gates'], [])

    def test_pr_head_mismatch_fails_and_ci_head_mismatch_is_missing(self):
        result_sha, _ = self._make_result()
        pr = {'state': 'open', 'merged': False,
              'head': {'sha': '9' * 40}, 'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        with patch('llmtool.task_completion._github_get', return_value=pr):
            with self.assertRaises(LLMToolError):
                task_completion.collect_github_evidence(
                    pr_number=10, result_sha=result_sha, executor_identity='executor-agent',
                    executor_session_ref='Executor D6 session',
                    executor_native_session_id='native-executor',
                    reviewer_task_id='reviewer-task', common_dir=self.common)
        reviewer_root = self.base / 'reviewer'
        self._git('worktree', 'add', '-b', 'review/branch', str(reviewer_root), result_sha)
        self._write_registration('reviewer-task', 'review-agent', 'Reviewer session',
            reviewer_root, 'review/branch', result_sha, result_sha,
            native_session_id='native-reviewer', github_login='reviewer-user')
        pr['head']['sha'] = result_sha
        reviews = [{'state': 'APPROVED', 'commit_id': result_sha,
                    'user': {'login': 'reviewer-user'},
                    'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT'}]
        checks = {'total_count': 1, 'check_runs': [
            {'name': 'check (3.9)', 'head_sha': '8' * 40,
             'status': 'completed', 'conclusion': 'success'}]}
        with patch('llmtool.task_completion._github_get', side_effect=[pr, reviews, checks]):
            evidence = task_completion.collect_github_evidence(
                pr_number=10, result_sha=result_sha, executor_identity='executor-agent',
                executor_session_ref='Executor D6 session',
                executor_native_session_id='native-executor',
                reviewer_task_id='reviewer-task', common_dir=self.common)
        self.assertIn('REQUIRED_CI_MISSING', evidence['missing_gates'])

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
             'review_commit_sha': '7' * 40, 'reviewer_d6_identity': 'review-agent',
             'review_session_identity': 'D6_NATIVE_SESSION_VERIFIED',
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


if __name__ == '__main__':
    unittest.main()
