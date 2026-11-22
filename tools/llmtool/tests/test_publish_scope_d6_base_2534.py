"""#2534: publish judges the change a Task adds, not D6 branch history main has.

A D6 registered on an old PR branch carries commits whose content main already
holds through a squash merge. The per-commit publish scope walk starts at the
integrator-registered base_sha only when the net change that base still makes
against main stays inside write_paths; every later commit is judged one by one
as before, and any doubt keeps the full walk (fail closed).
"""

import os
import unittest
from unittest.mock import patch

from llmtool import task_advance
import test_task_advance


class PublishScopeD6BaseTests(unittest.TestCase):
    # Reuse only the publish fixture (real git repo, bare remote, fake gh);
    # inheriting would run every TaskAdvancePublishTests case again.
    _FIXTURE = test_task_advance.TaskAdvancePublishTests
    TASK_ID = _FIXTURE.TASK_ID
    EXECUTOR_TASK = _FIXTURE.EXECUTOR_TASK
    BRANCH = _FIXTURE.BRANCH
    WRITE_PATHS = _FIXTURE.WRITE_PATHS
    setUp = _FIXTURE.setUp
    git = staticmethod(_FIXTURE.git)
    git_runner = staticmethod(_FIXTURE.git_runner)
    record_result = _FIXTURE.record_result
    gh = _FIXTURE.gh

    def write(self, name, text):
        with open(os.path.join(self.root, name), 'w', encoding='utf-8') as stream:
            stream.write(text)

    def commit(self, message, *paths):
        self.git(['add', '--', *paths], cwd=self.root)
        self.git(['commit', '-m', message], cwd=self.root)
        return self.git(['rev-parse', 'HEAD'], cwd=self.root).strip()

    def old_branch_history(self, *, squash_onto_main=True):
        """An old PR commit outside write scope, squash-merged into main, then main merged back."""
        # The branch is not yet published, like the #2534 D6 branch.
        self.git(['push', 'origin', '--delete', self.BRANCH], cwd=self.root)
        self.write('other.txt', 'old pr change\n')
        self.commit('old PR work outside this Task scope', 'other.txt')
        if squash_onto_main:
            # main receives the same content as a separate squash commit.
            self.git(['checkout', '-q', '-b', 'mainline', self.initial_head], cwd=self.root)
            self.write('other.txt', 'old pr change\n')
            self.commit('squash of old PR', 'other.txt')
            self.write('later.txt', 'main moved on\n')
            self.commit('later main work', 'later.txt')
            self.git(['push', 'origin', 'HEAD:refs/heads/main'], cwd=self.root)
            self.git(['checkout', '-q', self.BRANCH], cwd=self.root)
            self.git(['merge', '-q', '--no-edit', 'mainline'], cwd=self.root)
        return self.git(['rev-parse', 'HEAD'], cwd=self.root).strip()

    def publish_with_base(self, base_sha):
        record = {'task_id': self.TASK_ID, 'developer': {'write_paths': self.WRITE_PATHS}}
        d6_binding = {'root': self.root,
                      'common_dir': os.path.join(self.tmp.name, 'canonical', '.git'),
                      'branch': self.BRANCH, 'task_id': self.EXECUTOR_TASK,
                      'recorded_result_head': self.initial_head, 'base_sha': base_sha}
        return task_advance._publish_task(record, d6_binding, self.EXECUTOR_TASK,
            git_runner=self.git_runner, gh_runner=self.gh,
            record_result_call=self.record_result)

    def snapshot_from_registry(self, registered_base):
        """The advance path: _d6_snapshot over a real D6Registration (no hand-built d6)."""
        from llmtool import task_completion
        registration = task_completion.D6Registration(
            self.EXECUTOR_TASK, 'opencode-developer', 'session-ref', self.root,
            os.path.join(self.tmp.name, 'canonical', '.git'), self.BRANCH,
            registered_base, None, 'ACTIVE', 'LOCKED', 1, 1, os.getuid(),
            None, None, None, None)
        record = {'task_id': self.TASK_ID, 'completion': {'evidence': {
            'common_dir': os.path.join(self.tmp.name, 'canonical', '.git')}}}
        with patch.object(task_completion, 'read_d6_registration', return_value=registration):
            return task_advance._d6_snapshot(record, self.EXECUTOR_TASK)

    def publish_through_snapshot(self, registered_base):
        d6 = self.snapshot_from_registry(registered_base)
        self.assertEqual(d6.get('base_sha'), registered_base)
        record = {'task_id': self.TASK_ID, 'developer': {'write_paths': self.WRITE_PATHS}}
        return task_advance._publish_task(record, d6, self.EXECUTOR_TASK,
            git_runner=self.git_runner, gh_runner=self.gh,
            record_result_call=self.record_result)

    def test_advance_snapshot_carries_registry_base_and_merge_commit_base_publishes(self):
        # The real #2534 shape: the registered base is a merge commit (main
        # merged into the old PR branch) and the branch was never pushed.
        base = self.old_branch_history()
        parents = self.git(['rev-list', '--parents', '-n', '1', base], cwd=self.root).split()
        self.assertEqual(len(parents), 3)
        self.write('allowed.txt', 'task change\n')
        result = self.publish_through_snapshot(base)
        self.assertEqual(len(self.recorded), 1)
        self.assertIsInstance(result, dict)

    def test_advance_snapshot_with_base_not_on_main_still_refuses(self):
        base = self.old_branch_history(squash_onto_main=False)
        self.write('allowed.txt', 'task change\n')
        with self.assertRaises(task_advance._PublishRefusal) as caught:
            self.publish_through_snapshot(base)
        self.assertEqual(caught.exception.args[0], 'PUBLISH_OUT_OF_SCOPE')
        self.assertEqual(self.recorded, [])

    def assert_out_of_scope(self, base_sha):
        with self.assertRaises(task_advance._PublishRefusal) as caught:
            self.publish_with_base(base_sha)
        self.assertEqual(caught.exception.args[0], 'PUBLISH_OUT_OF_SCOPE')
        self.assertEqual(self.recorded, [])

    def test_2534_squash_merged_history_with_in_scope_change_publishes(self):
        base = self.old_branch_history()
        self.write('allowed.txt', 'task change\n')
        result = self.publish_with_base(base)
        self.assertEqual(len(self.recorded), 1)
        self.assertIsInstance(result, dict)
        pushed = self.git(['ls-remote', '--heads', 'origin', self.BRANCH], cwd=self.root)
        self.assertTrue(pushed.strip())

    def test_without_registered_base_the_full_walk_still_refuses(self):
        self.old_branch_history()
        self.write('allowed.txt', 'task change\n')
        self.assert_out_of_scope(None)

    def test_task_commit_after_base_outside_scope_is_refused(self):
        base = self.old_branch_history()
        self.write('other.txt', 'task changes it again\n')
        self.commit('task touches out-of-scope file', 'other.txt')
        self.write('allowed.txt', 'task change\n')
        self.assert_out_of_scope(base)

    def test_uncommitted_out_of_scope_change_is_refused(self):
        base = self.old_branch_history()
        self.write('other.txt', 'task edits it\n')
        self.assert_out_of_scope(base)

    def test_base_history_not_on_main_is_refused(self):
        base = self.old_branch_history(squash_onto_main=False)
        self.write('allowed.txt', 'task change\n')
        self.assert_out_of_scope(base)

    def test_base_that_is_not_an_ancestor_is_ignored(self):
        self.old_branch_history()
        self.write('allowed.txt', 'task change\n')
        self.assert_out_of_scope('f' * 40)


if __name__ == '__main__':
    unittest.main()
