import json
import subprocess
import unittest
from unittest.mock import patch

from llmtool import task_advance
from test_task_advance import HEAD, base_record, d6


class TaskAdvancePublishTests(unittest.TestCase):
    def setUp(self):
        self.record = base_record()
        self.snapshot = dict(d6(), branch='feature/task', dirty=False)
        self.gh_calls = []

    def _gh(self, _root, args):
        self.gh_calls.append(list(args))
        if args[:2] == ['pr', 'list']:
            return subprocess.CompletedProcess(['gh', *args], 0,
                json.dumps([{'number': 41, 'headRefOid': HEAD}]).encode(), b'')
        raise AssertionError('unexpected gh call')

    def _advance(self, *, pr_number=None, gh=None):
        store = type('Store', (), {'get': lambda _self, _task_id: self.record})()
        return task_advance.advance(task_id=self.record['task_id'], store=store,
            pr_number=pr_number, publish_gh_runner=gh or self._gh,
            github_get=lambda path: {'number': 41 if path == 'pulls/41' else 17,
                'head': {'sha': HEAD}, 'state': 'open', 'merged': False})

    def test_clean_recorded_d6_looks_up_existing_pr_and_advances_remote_test(self):
        with patch.object(task_advance, '_d6_snapshot', return_value=self.snapshot), \
             patch.object(task_advance.task_submission_cli, 'run_task_remote_test', return_value=0):
            result = self._advance()
        self.assertEqual(result['step'], 'RUN_REMOTE_TEST')
        self.assertEqual(self.gh_calls, [[
            'pr', 'list', '--head', 'feature/task', '--state', 'all',
            '--json', 'number,headRefOid']])

    def test_explicit_pr_skips_lookup(self):
        with patch.object(task_advance, '_d6_snapshot', return_value=self.snapshot), \
             patch.object(task_advance, '_publish_run_gh', side_effect=AssertionError('lookup')):
            result = self._advance(pr_number=17)
        self.assertEqual(result['step'], 'RUN_REMOTE_TEST')
        self.assertEqual(self.gh_calls, [])

    def test_lookup_failure_leaves_publish_selected(self):
        def failed(_root, args):
            self.gh_calls.append(list(args))
            return subprocess.CompletedProcess(['gh', *args], 1, b'', b'failed')
        with patch.object(task_advance, '_d6_snapshot', return_value=self.snapshot), \
             patch.object(task_advance, '_publish_task', return_value={'pr': 42, 'head': HEAD}) as publish:
            result = self._advance(gh=failed)
        self.assertEqual(result['pr'], 42)
        publish.assert_called_once()

    def test_publication_title_and_body_from_pm_record(self):
        self.record.update(response=json.dumps({'summary': 'Repair task advance\nextra'}),
            pm_assignment={'goal': 'Find the published PR and prepare review.',
                           'acceptance': ['Find exactly one PR at this head.', 'Keep explicit PR.']})
        title, body = task_advance._publication_text(
            self.record, 'executor-1', 'feature/task')
        self.assertEqual(title, 'Repair task advance extra')
        self.assertEqual(body, '\n'.join([
            'Repair task advance\nextra', '',
            'Find the published PR and prepare review.', '', 'Acceptance:',
            '- Find exactly one PR at this head.', '- Keep explicit PR.', '',
            f'Task ID: {self.record["task_id"]}', 'D6 executor: executor-1',
            'Branch: feature/task']))
        long_record = dict(self.record, response=json.dumps({'summary': 's' * 130}))
        long_title, _ = task_advance._publication_text(
            long_record, 'executor-1', 'feature/task')
        self.assertEqual(long_title, 's' * 120)

    def test_unreadable_response_uses_legacy_publication_text(self):
        self.record.update(response='not json', pm_assignment={'goal': 'goal'})
        self.assertEqual(task_advance._publication_text(
            self.record, 'executor-1', 'feature/task'),
            (f'llmtool Task {self.record["task_id"]}',
             f'Automated publication for llmtool Task {self.record["task_id"]}.'))

    def test_create_pr_uses_main_base_and_task_text(self):
        calls = []
        def gh(_root, args):
            calls.append(list(args))
            if args[:2] == ['pr', 'list']:
                pulls = [] if len(calls) == 1 else [{'number': 41, 'headRefOid': HEAD}]
                return subprocess.CompletedProcess(['gh', *args], 0,
                    json.dumps(pulls).encode(), b'')
            if args[:2] == ['pr', 'create']:
                return subprocess.CompletedProcess(['gh', *args], 0, b'', b'')
            raise AssertionError('unexpected gh call')
        number = task_advance._publish_pr(gh, '/d6', 'feature/task',
            self.record['task_id'], HEAD, 'Task summary', 'Task body')
        self.assertEqual(number, 41)
        create = next(args for args in calls if args[:2] == ['pr', 'create'])
        self.assertEqual(create, ['pr', 'create', '--head', 'feature/task',
            '--base', 'main', '--title', 'Task summary', '--body', 'Task body'])


if __name__ == '__main__':
    unittest.main()
