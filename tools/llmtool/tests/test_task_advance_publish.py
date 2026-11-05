import json
import subprocess
import unittest
from unittest.mock import call, patch

from llmtool import task_advance
from test_task_advance import HEAD, base_record, d6


class TaskAdvancePublishTests(unittest.TestCase):
    def setUp(self):
        self.record = base_record()
        self.record['remote_test_outcomes'] = []
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

    def test_stale_open_pr_head_catches_up_on_second_read(self):
        old_head = 'b' * 40
        pulls = [
            [{'number': 41, 'headRefOid': old_head}],
            [{'number': 41, 'headRefOid': HEAD}],
        ]

        def gh(_root, args):
            self.gh_calls.append(list(args))
            self.assertEqual(args[:2], ['pr', 'list'])
            return subprocess.CompletedProcess(['gh', *args], 0,
                json.dumps(pulls.pop(0)).encode(), b'')

        with patch.object(task_advance, '_publish_pause') as pause:
            number = task_advance._publish_pr(gh, '/d6', 'feature/task',
                self.record['task_id'], HEAD, 'Task summary', 'Task body')
        self.assertEqual(number, 41)
        self.assertEqual(len(self.gh_calls), 2)
        pause.assert_called_once_with(2)

    def test_stale_open_pr_head_fails_after_four_reads_without_sleep(self):
        old_head = 'b' * 40

        def gh(_root, args):
            self.gh_calls.append(list(args))
            self.assertEqual(args[:2], ['pr', 'list'])
            return subprocess.CompletedProcess(['gh', *args], 0,
                json.dumps([{'number': 41, 'headRefOid': old_head}]).encode(), b'')

        with patch.object(task_advance, '_publish_pause') as pause:
            with self.assertRaises(task_advance._PublishRefusal) as caught:
                task_advance._publish_pr(gh, '/d6', 'feature/task',
                    self.record['task_id'], HEAD, 'Task summary', 'Task body')
        self.assertEqual(caught.exception.code, 'PUBLISH_GITHUB_FAILED')
        self.assertEqual(len(self.gh_calls), 4)
        self.assertEqual(pause.call_args_list, [call(2)] * 3)

    def _fake_publish(self, *, main_state='contained', fetch_failed=False):
        initial_head, committed_head, merged_head = 'a' * 40, 'b' * 40, 'c' * 40
        state = {'head': initial_head}
        events = []
        git_calls = []

        def result(args, code=0, output=b''):
            return subprocess.CompletedProcess(['git', *args], code, output, b'')

        def git(_root, args):
            args = list(args)
            git_calls.append(args)
            events.append(('git', tuple(args)))
            if args == ['rev-parse', '--show-toplevel']:
                return result(args, output=b'/fake/d6\n')
            if args == ['branch', '--show-current']:
                return result(args, output=b'feature/task\n')
            if args in (['ls-files', '--others', '--ignored', '--exclude-standard', '-z'],
                        ['ls-files', '--others', '--exclude-standard', '-z']):
                return result(args)
            if args == ['diff', '--no-renames', '--name-only', '-z', 'HEAD']:
                return result(args, output=b'allowed.txt\0')
            if args == ['ls-remote', '--heads', 'origin', 'refs/heads/feature/task']:
                return result(args, output=f'{initial_head}\trefs/heads/feature/task\n'.encode())
            if args == ['rev-parse', '--verify', 'HEAD']:
                return result(args, output=(state['head'] + '\n').encode())
            if args == ['merge-base', '--is-ancestor', initial_head, 'HEAD']:
                return result(args)
            if args == ['merge-base', '--is-ancestor', 'origin/main', 'HEAD']:
                return result(args, 0 if main_state == 'contained' else 1)
            if args == ['add', '--', 'allowed.txt']:
                return result(args)
            if args == ['diff', '--cached', '--no-renames', '--name-only', '-z', 'HEAD']:
                return result(args, output=b'allowed.txt\0')
            if args[:1] == ['commit']:
                state['head'] = committed_head
                return result(args)
            if args == ['fetch', 'origin', 'main']:
                return result(args, 1 if fetch_failed else 0)
            if args == ['merge', '--no-edit', 'origin/main']:
                if main_state == 'conflict':
                    return result(args, 1)
                state['head'] = merged_head
                return result(args)
            if args == ['ls-files', '--unmerged', '-z']:
                return result(args, output=b'100644 conflict\0' if main_state == 'conflict' else b'')
            if args == ['merge', '--abort']:
                events.append(('abort', tuple(args)))
                return result(args)
            if args == ['push', 'origin', 'HEAD:refs/heads/feature/task']:
                events.append(('push', state['head']))
                return result(args)
            raise AssertionError(f'unexpected git call: {args}')

        def record_result(**_kwargs):
            events.append(('record', state['head']))
            return state['head']

        def gh(_root, args):
            if args[:2] == ['pr', 'list']:
                return subprocess.CompletedProcess(['gh', *args], 0,
                    json.dumps([{'number': 23, 'headRefOid': state['head']}]).encode(), b'')
            raise AssertionError(f'unexpected gh call: {args}')

        record = {'task_id': '1' * 32, 'developer': {'write_paths': ['allowed.txt']}}
        binding = {'root': '/fake/d6', 'common_dir': '/fake/canonical/.git',
                   'branch': 'feature/task', 'task_id': '3' * 32,
                   'recorded_result_head': initial_head}
        try:
            value = task_advance._publish_task(record, binding, '3' * 32,
                git_runner=git, gh_runner=gh, record_result_call=record_result)
        except task_advance._PublishRefusal as exc:
            value = exc.code
        return value, events, git_calls, committed_head, merged_head

    def test_main_already_contained_does_not_merge(self):
        result, events, git_calls, committed, _merged = self._fake_publish()
        self.assertEqual(result['head'], committed)
        self.assertIn(['merge-base', '--is-ancestor', 'origin/main', 'HEAD'], git_calls)
        self.assertNotIn(['merge', '--no-edit', 'origin/main'], git_calls)

    def test_moved_main_merges_then_records_then_pushes_new_head(self):
        result, events, _git_calls, _committed, merged = self._fake_publish(main_state='moved')
        self.assertEqual(result['head'], merged)
        merge = events.index(('git', ('merge', '--no-edit', 'origin/main')))
        record = events.index(('record', merged))
        push = events.index(('push', merged))
        self.assertLess(merge, record)
        self.assertLess(record, push)

    def test_merge_conflict_aborts_and_never_pushes(self):
        result, events, _git_calls, _committed, _merged = self._fake_publish(main_state='conflict')
        self.assertEqual(result, 'PUBLISH_MERGE_CONFLICT')
        self.assertIn(('abort', ('merge', '--abort')), events)
        self.assertFalse(any(event[0] == 'push' for event in events))

    def test_fetch_failure_refuses_without_push(self):
        result, events, _git_calls, _committed, _merged = self._fake_publish(fetch_failed=True)
        self.assertEqual(result, 'PUBLISH_COMMAND_FAILED')
        self.assertFalse(any(event[0] == 'push' for event in events))


if __name__ == '__main__':
    unittest.main()
