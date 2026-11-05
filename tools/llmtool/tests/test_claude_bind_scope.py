import io
import json
import subprocess
import unittest
from argparse import Namespace
from unittest.mock import patch

import test_task_completion as completion_fixture

from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import run_task_record_pm
from llmtool import task_submission_cli
from llmtool import worker_dispatch


def setUpModule():
    completion_fixture.review_trust_fixture.start_module()


def tearDownModule():
    completion_fixture.review_trust_fixture.stop_module()


class ClaudeBindScopeTests(unittest.TestCase):
    owner_ref = ('https://github.com/davidcatcat/davidcattoolsproject/'
                 'issues/289#issuecomment-123456789')
    d6_task_id = 'c' * 32
    native_session_id = '12345678-1234-1234-1234-123456789abc'

    def setUp(self):
        self.fx = completion_fixture.TaskCompletionTests(methodName='runTest')
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.store = TaskStore((self.fx.base / 'claude-tasks').resolve())
        self.assertEqual(worker_dispatch._POLICY_PATH.resolve(),
                         self.fx.policy_path.resolve())
        policy = json.loads(self.fx.policy_path.read_text(encoding='utf-8'))
        policy['workers']['claude-developer']['dispatch'] = 'OWNER_ASSIGNMENT_ONLY'
        policy['workers']['claude-developer']['owner_assignments'] = [{
            'task_id': self.d6_task_id,
            'owner_assignment_ref': self.owner_ref,
        }]
        self.fx.policy_path.write_text(json.dumps(policy), encoding='utf-8')
        decision = worker_dispatch.evaluate(
            'claude-developer', owner_assignment_ref=self.owner_ref,
            task_id=self.d6_task_id)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason_code, 'OWNER_ASSIGNMENT_MATCHED')

    def _task(self, risk='LOW'):
        response = json.dumps({
            'schema_version': 1,
            'summary': 'Bind and verify a synthetic Claude D6 result.',
            'developer_assignment': {
                'route': 'claude', 'risk': risk,
                'goal': 'Record the bounded committed result.',
                'acceptance': ['The synthetic result is scoped and recorded.'],
            },
        })
        envelope = json.dumps({
            'request': 'Verify the committed synthetic D6 result.',
            'response': response,
        })
        return run_task_record_pm(
            Namespace(stdin_json=True), store=self.store,
            input_stream=io.StringIO(envelope), return_record=True)

    def _register(self, head=None):
        self.fx._write_registration(
            self.d6_task_id, 'claude-developer', 'Claude synthetic D6 session',
            self.fx.root, 'feat/executor', self.fx.source_sha,
            self.fx._git('rev-parse', 'HEAD') if head is None else head,
            native_session_id=self.native_session_id)

    def _commit_path(self, path, content, message):
        target = self.fx.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        self.fx._git('add', path)
        self.fx._git('commit', '-m', message)
        head = self.fx._git('rev-parse', 'HEAD')
        self._register(head)
        return head

    def _main_commit(self, *, fixture_content=None, readme_content='Main update.\n'):
        self.fx._git('branch', 'main', self.fx.source_sha)
        self.fx._git('checkout', 'main')
        if fixture_content is not None:
            (self.fx.root / self.fx.source_path).write_text(
                fixture_content, encoding='utf-8')
            self.fx._git('add', self.fx.source_path)
        (self.fx.root / 'README.md').write_text(readme_content, encoding='utf-8')
        self.fx._git('add', 'README.md')
        self.fx._git('commit', '-m', 'main update')
        main_sha = self.fx._git('rev-parse', 'HEAD')
        self.fx._git('checkout', 'feat/executor')
        return main_sha

    def _developer_edit(self, content='Developer fixture update.\n'):
        if content == 'Developer fixture update.\n':
            head, _ = self.fx._make_result()
            self._register(head)
            return head
        target = self.fx.root / self.fx.source_path
        target.write_text(content, encoding='utf-8')
        self.fx._git('add', self.fx.source_path)
        self.fx._git('commit', '-m', 'developer fixture update')
        self._register()
        return self.fx._git('rev-parse', 'HEAD')

    def _merge(self, branch, message='merge main'):
        self.fx._git('merge', '--no-ff', branch, '-m', message)
        head = self.fx._git('rev-parse', 'HEAD')
        self._register(head)
        return head

    def _github_main_pin(self, sha, object_type='commit'):
        def github_get(path):
            if path != 'git/ref/heads/main':
                raise AssertionError(f'unexpected GitHub path: {path}')
            return {'ref': 'refs/heads/main',
                    'object': {'type': object_type, 'sha': sha}}
        return patch.object(completion_fixture.task_completion, '_github_get',
                            side_effect=github_get)

    def _bind(self, task, *, head=None, store=None):
        args = Namespace(
            task_id=task['task_id'], executor_task=self.d6_task_id,
            owner_ref=self.owner_ref, common_dir=self.fx.common,
            read_path=[self.fx.source_path], write_path=[self.fx.source_path])
        return task_submission_cli.run_task_claude_bind(
            args, store=self.store if store is None else store,
            stdout=io.StringIO())

    def _result(self, task, head):
        return task_submission_cli.run_task_claude_result(Namespace(
            task_id=task['task_id'], executor_task=self.d6_task_id,
            head=head, pm_session=task['pm_session_id']),
            store=self.store, stdout=io.StringIO())

    def _bound_result_history(self):
        task = self._task()
        main_sha = self._main_commit()
        self._developer_edit()
        self._merge('main')
        head = self.fx._git('rev-parse', 'HEAD')
        with self._github_main_pin(main_sha):
            self._bind(task)
        return task, main_sha, head

    def test_main_merge_changes_are_excluded_from_developer_scope(self):
        task, main_sha, head = self._bound_result_history()
        with self._github_main_pin(main_sha):
            self._result(task, head)
        completed = self.store.get(task['task_id'])
        self.assertEqual(completed['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(completed['developer']['claude_result']['head_sha'], head)
        self.assertEqual(completion_fixture.task_completion._developer_changed_paths(
            str(self.fx.root), self.fx.source_sha, head, main_sha),
            (self.fx.source_path,))

    def test_main_only_merge_has_no_bounded_developer_result(self):
        task = self._task()
        main_sha = self._main_commit()
        self._merge('main')
        with self._github_main_pin(main_sha):
            with self.assertRaisesRegex(
                    LLMToolError, 'Claude D6 binding requires a bounded committed result diff'):
                self._bind(task)
        unchanged = self.store.get(task['task_id'])
        self.assertIsNone(unchanged['developer'])
        self.assertIsNone(unchanged['completion'])

    def test_foreign_merge_is_out_of_scope(self):
        task = self._task()
        main_sha = self._main_commit()
        self.fx._git('branch', 'foreign', self.fx.source_sha)
        self.fx._git('checkout', 'foreign')
        self._commit_path('outside.txt', 'foreign content\n', 'foreign edit')
        self.fx._git('checkout', 'feat/executor')
        self._developer_edit()
        self._merge('foreign', 'merge foreign branch')
        self._register()
        with self._github_main_pin(main_sha):
            with self.assertRaisesRegex(LLMToolError, 'RESULT_SCOPE_MISMATCH'):
                self._bind(task)

    def test_out_of_scope_merge_resolution_is_refused(self):
        task = self._task()
        main_sha = self._main_commit(
            fixture_content='import unittest\nclass Fixture(unittest.TestCase):\n'
                            '    def test_main(self): self.assertTrue(True)\n')
        self._developer_edit(
            'import unittest\nclass Fixture(unittest.TestCase):\n'
            '    def test_developer(self): self.assertTrue(True)\n')
        merged = subprocess.run(
            ['/usr/bin/git', 'merge', '--no-ff', 'main', '-m', 'merge main'],
            cwd=self.fx.root, text=True, capture_output=True)
        self.assertNotEqual(merged.returncode, 0)
        (self.fx.root / self.fx.source_path).write_text(
            'import unittest\nclass Fixture(unittest.TestCase):\n'
            '    def test_resolved(self): self.assertTrue(True)\n', encoding='utf-8')
        (self.fx.root / 'README.md').write_text('Developer resolution edit.\n',
                                                encoding='utf-8')
        self.fx._git('add', self.fx.source_path, 'README.md')
        self.fx._git('commit', '-m', 'resolve merge out of scope')
        self._register()
        with self._github_main_pin(main_sha):
            with self.assertRaisesRegex(LLMToolError, 'RESULT_SCOPE_MISMATCH'):
                self._bind(task)

    def test_commit_after_merge_is_out_of_scope_at_bind(self):
        task = self._task()
        main_sha = self._main_commit()
        self._developer_edit()
        self._merge('main')
        self._commit_path('outside.txt', 'late out of scope\n', 'late edit')
        with self._github_main_pin(main_sha):
            with self.assertRaisesRegex(Exception, 'RESULT_SCOPE_MISMATCH'):
                self._bind(task)

    def test_commit_after_merge_is_out_of_scope_at_result(self):
        task, main_sha, head = self._bound_result_history()
        before = self.store.get(task['task_id'])
        self._commit_path('outside.txt', 'late out of scope\n', 'late edit')
        late_head = self.fx._git('rev-parse', 'HEAD')
        with self._github_main_pin(main_sha):
            with self.assertRaisesRegex(LLMToolError, 'RESULT_SCOPE_MISMATCH'):
                self._result(task, late_head)
        after = self.store.get(task['task_id'])
        self.assertEqual(after['record_sha256'], before['record_sha256'])
        self.assertEqual(after['developer']['state'], 'DEVELOPER_PENDING')
        self.assertEqual(after['completion']['state'], 'WAITING_EXECUTOR')
        self.assertNotIn('claude_result', after['developer'])

    def test_invalid_main_pin_is_refused_at_bind_and_result(self):
        task = self._task()
        main_sha = self._main_commit()
        self._developer_edit()
        head = self._merge('main')
        before = self.store.get(task['task_id'])
        with self._github_main_pin(main_sha, object_type='tag'):
            with self.assertRaises(LLMToolError) as raised:
                self._bind(task)
        self.assertEqual(raised.exception.completion_code, 'MAIN_REF_PIN_INVALID')
        self.assertEqual(self.store.get(task['task_id'])['record_sha256'],
                         before['record_sha256'])

        with self._github_main_pin(main_sha):
            self._bind(task)
        before = self.store.get(task['task_id'])
        with self._github_main_pin(main_sha, object_type='tag'):
            with self.assertRaises(LLMToolError) as raised:
                self._result(task, head)
        self.assertEqual(raised.exception.completion_code, 'MAIN_REF_PIN_INVALID')
        self.assertEqual(self.store.get(task['task_id'])['record_sha256'],
                         before['record_sha256'])

    def test_result_head_mismatch_leaves_task_unchanged(self):
        task, main_sha, head = self._bound_result_history()
        before = self.store.get(task['task_id'])
        with self._github_main_pin(main_sha):
            with self.assertRaisesRegex(LLMToolError, 'CLAUDE_RESULT_STATE_INVALID'):
                self._result(task, self.fx.source_sha)
        self.assertEqual(self.store.get(task['task_id'])['record_sha256'],
                         before['record_sha256'])

    def test_dirty_d6_is_refused_at_bind(self):
        task = self._task()
        self._developer_edit()
        head = self.fx._git('rev-parse', 'HEAD')
        self._register(head)
        (self.fx.root / self.fx.source_path).write_text('uncommitted edit\n',
                                                       encoding='utf-8')
        with self.assertRaisesRegex(
                LLMToolError, 'Claude D6 binding requires a clean registered D6 HEAD'):
            self._bind(task)
        unchanged = self.store.get(task['task_id'])
        self.assertIsNone(unchanged['developer'])
        self.assertIsNone(unchanged['completion'])

    def test_other_risk_pm_response_is_refused_without_task(self):
        task_ids_before = self.store.task_ids()
        with self.assertRaisesRegex(
                LLMToolError,
                'PM response must recommend one low-risk supported Developer assignment'):
            self._task(risk='OTHER')
        self.assertEqual(self.store.task_ids(), task_ids_before)

    def test_other_risk_stored_assignment_is_refused_at_bind(self):
        task = self._task(risk='LOW')
        stored = self.store.get(task['task_id'])
        self.assertEqual(stored['pm_assignment']['risk'], 'LOW')
        self.assertIsNone(stored['developer'])
        self.assertIsNone(stored['completion'])

        class ReadOnlyRiskStore:
            def get(self, task_id):
                if task_id != task['task_id']:
                    raise AssertionError('unexpected task ID')
                result = dict(stored)
                result['pm_assignment'] = dict(
                    stored['pm_assignment'], risk='OTHER')
                return result

            def __getattr__(self, name):
                raise AssertionError(f'unexpected store access: {name}')

        with self.assertRaisesRegex(
                LLMToolError, 'Claude binding requires a low-risk active-PM Claude Task'):
            self._bind(task, store=ReadOnlyRiskStore())

    def test_dirty_d6_is_refused_at_result(self):
        task, main_sha, head = self._bound_result_history()
        before = self.store.get(task['task_id'])
        (self.fx.root / self.fx.source_path).write_text(
            'uncommitted edit\n', encoding='utf-8')
        with self._github_main_pin(main_sha):
            with self.assertRaisesRegex(LLMToolError, 'CLAUDE_RESULT_STATE_INVALID'):
                self._result(task, head)
        after = self.store.get(task['task_id'])
        self.assertEqual(after['record_sha256'], before['record_sha256'])
        self.assertEqual(after['developer']['state'], 'DEVELOPER_PENDING')
        self.assertEqual(after['completion']['state'], 'WAITING_EXECUTOR')
        self.assertNotIn('claude_result', after['developer'])

    def test_foreign_merge_is_out_of_scope_at_result(self):
        task, main_sha, _ = self._bound_result_history()
        self.fx._git('branch', 'foreign', self.fx.source_sha)
        self.fx._git('checkout', 'foreign')
        self._commit_path('outside.txt', 'foreign content\n', 'foreign edit')
        self.fx._git('checkout', 'feat/executor')
        head = self._merge('foreign', 'merge foreign branch')
        before = self.store.get(task['task_id'])
        with self._github_main_pin(main_sha):
            with self.assertRaisesRegex(LLMToolError, 'RESULT_SCOPE_MISMATCH'):
                self._result(task, head)
        after = self.store.get(task['task_id'])
        self.assertEqual(after['record_sha256'], before['record_sha256'])
        self.assertEqual(after['developer']['state'], 'DEVELOPER_PENDING')
        self.assertEqual(after['completion']['state'], 'WAITING_EXECUTOR')
        self.assertNotIn('claude_result', after['developer'])

    def test_out_of_scope_merge_resolution_is_refused_at_result(self):
        task = self._task()
        main_sha = self._main_commit(
            fixture_content='import unittest\nclass Fixture(unittest.TestCase):\n'
                            '    def test_main(self): self.assertTrue(True)\n')
        self._developer_edit(
            'import unittest\nclass Fixture(unittest.TestCase):\n'
            '    def test_developer(self): self.assertTrue(True)\n')
        with self._github_main_pin(main_sha):
            self._bind(task)

        merged = subprocess.run(
            ['/usr/bin/git', 'merge', '--no-ff', 'main', '-m', 'merge main'],
            cwd=self.fx.root, text=True, capture_output=True)
        self.assertNotEqual(merged.returncode, 0)
        unmerged = self.fx._git('diff', '--name-only', '--diff-filter=U').splitlines()
        self.assertEqual(unmerged, [self.fx.source_path])
        (self.fx.root / self.fx.source_path).write_text(
            'import unittest\nclass Fixture(unittest.TestCase):\n'
            '    def test_resolved(self): self.assertTrue(True)\n', encoding='utf-8')
        (self.fx.root / 'README.md').write_text(
            'Developer resolution edit.\n', encoding='utf-8')
        self.fx._git('add', self.fx.source_path, 'README.md')
        self.fx._git('commit', '-m', 'resolve merge out of scope')
        head = self.fx._git('rev-parse', 'HEAD')
        self._register(head)

        before = self.store.get(task['task_id'])
        with self._github_main_pin(main_sha):
            with self.assertRaisesRegex(LLMToolError, 'RESULT_SCOPE_MISMATCH'):
                self._result(task, head)
        after = self.store.get(task['task_id'])
        self.assertEqual(after['record_sha256'], before['record_sha256'])
        self.assertEqual(after['developer']['state'], 'DEVELOPER_PENDING')
        self.assertEqual(after['completion']['state'], 'WAITING_EXECUTOR')
        self.assertNotIn('claude_result', after['developer'])


if __name__ == '__main__':
    unittest.main()
