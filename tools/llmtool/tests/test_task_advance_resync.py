import json
import subprocess
import unittest
from unittest.mock import patch

from llmtool import task_advance

try:
    from . import test_task_advance as advance_tests
except ImportError:
    import test_task_advance as advance_tests

HEAD = advance_tests.HEAD
MAIN_SHA = advance_tests.MAIN_SHA
PR = advance_tests.PR
base_record = advance_tests.base_record
d6 = advance_tests.d6
remote_receipt = advance_tests.remote_receipt
with_current_main = advance_tests.with_current_main
green_linux_ci = advance_tests.green_linux_ci

NEW_HEAD = 'c' * 40
BRANCH = 'feature/task'


class ReadyResyncStore:
    def __init__(self, record):
        self.record = record

    def get(self, _task_id):
        return self.record


def ready_record(**changes):
    claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
             'review_result': {'verdict': 'PASS'}}
    event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
             'outcome': 'REVIEW_PASS'}
    record = base_record(
        developer={'state': 'DEVELOPER_RESPONDED', 'write_paths': ['allowed.txt']},
        review_dispatch_claim=[claim],
        completion_events=[event],
        completion={
            'executor_task_id': 'executor-1',
            'state': 'PM_EVIDENCE_READY',
            'evidence': {
                'result': {'result_head': HEAD},
                'review_pr_ci': {'stage': 'READY'},
            },
        },
    )
    record.update(changes)
    return record


def ready_d6(head=HEAD):
    snapshot = dict(d6(recorded=head))
    snapshot.update(
        root='/fake/d6',
        common_dir='/fake/canonical/.git',
        branch=BRANCH,
        result_head=head,
    )
    return snapshot


class TaskAdvanceReadyResyncTests(unittest.TestCase):
    def _github_get(self, *, main_reply=None, compare_reply=None, pr=None):
        pr_value = dict(PR, state='open') if pr is None else pr
        main_value = {'commit': {'sha': MAIN_SHA}} if main_reply is None else main_reply
        compare_value = {'status': 'ahead'} if compare_reply is None else compare_reply
        reads = []

        def read(path):
            reads.append(path)
            if path == 'branches/main':
                if isinstance(main_value, Exception):
                    raise main_value
                return main_value
            if path == f'compare/{MAIN_SHA}...{HEAD}':
                if isinstance(compare_value, Exception):
                    raise compare_value
                return compare_value
            if path == 'pulls/17':
                return pr_value
            return []

        return read, reads

    def _fake_runners(self, *, merge_tree_code=0, fetch_code=0):
        state = {'head': HEAD}
        git_calls = []
        gh_calls = []
        recorded = []
        events = []

        def completed(tool, args, code=0, output=b''):
            return subprocess.CompletedProcess([tool, *args], code, output, b'')

        def git_runner(_root, args):
            args = list(args)
            git_calls.append(args)
            events.append(('git', tuple(args)))
            if args == ['fetch', 'origin', 'main']:
                if isinstance(fetch_code, Exception):
                    raise fetch_code
                return completed('git', args, code=fetch_code)
            if args == ['merge-tree', '--write-tree', 'origin/main', 'HEAD']:
                if isinstance(merge_tree_code, Exception):
                    raise merge_tree_code
                return completed('git', args, code=merge_tree_code,
                                 output=(NEW_HEAD + '\n').encode())
            if args == ['rev-parse', '--show-toplevel']:
                return completed('git', args, output=b'/fake/d6\n')
            if args == ['branch', '--show-current']:
                return completed('git', args, output=(BRANCH + '\n').encode())
            if args in (
                ['ls-files', '--others', '--ignored', '--exclude-standard', '-z'],
                ['ls-files', '--others', '--exclude-standard', '-z'],
                ['diff', '--no-renames', '--name-only', '-z', 'HEAD'],
            ):
                return completed('git', args)
            if args == ['ls-remote', '--heads', 'origin', f'refs/heads/{BRANCH}']:
                return completed(
                    'git', args,
                    output=f'{HEAD}\trefs/heads/{BRANCH}\n'.encode(),
                )
            if args == ['rev-parse', '--verify', 'HEAD']:
                return completed('git', args, output=(state['head'] + '\n').encode())
            if args == ['merge-base', '--is-ancestor', HEAD, 'HEAD']:
                return completed('git', args, code=0)
            if args == ['merge-base', '--is-ancestor', 'origin/main', 'HEAD']:
                return completed('git', args, code=1)
            if args == ['merge', '--no-edit', 'origin/main']:
                state['head'] = NEW_HEAD
                events.append(('merge', NEW_HEAD))
                return completed('git', args, code=0)
            if args == ['push', 'origin', f'HEAD:refs/heads/{BRANCH}']:
                events.append(('push', state['head']))
                return completed('git', args, code=0)
            raise AssertionError(f'unexpected git call: {args}')

        def gh_runner(_root, args):
            args = list(args)
            gh_calls.append(args)
            if args[:2] == ['pr', 'list']:
                pulls = [{'number': 17, 'headRefOid': state['head']}]
                return completed('gh', args, output=json.dumps(pulls).encode())
            raise AssertionError(f'unexpected gh call: {args}')

        def record_result(**kwargs):
            recorded.append((kwargs, state['head']))
            events.append(('record', state['head']))
            return state['head']

        return state, git_calls, gh_calls, recorded, events, git_runner, gh_runner, record_result

    def test_ready_pr_ahead_or_identical_stays_waiting_integration_with_no_git_call(self):
        for status in ('ahead', 'identical'):
            with self.subTest(status=status):
                record = ready_record()
                store = ReadyResyncStore(record)
                get, reads = self._github_get(compare_reply={'status': status})
                git_calls = []
                with patch.object(task_advance, '_d6_snapshot', return_value=ready_d6()), \
                     patch.object(task_advance, '_review_outcomes', return_value=[]):
                    result = task_advance.advance(
                        task_id=record['task_id'],
                        store=store,
                        pr_number=17,
                        github_get=get,
                        publish_git_runner=lambda _root, args: git_calls.append(list(args)),
                    )
                self.assertEqual(result['step'], 'READY_FOR_INTEGRATION')
                self.assertEqual(result['next'], 'READY_FOR_INTEGRATION')
                self.assertEqual(result['codes'], ['WAITING_INTEGRATION'])
                self.assertEqual(git_calls, [])
                self.assertIn(f'compare/{MAIN_SHA}...{HEAD}', reads)

    def test_merged_pr_in_ready_for_integration_still_closes(self):
        record = ready_record()
        store = ReadyResyncStore(record)
        merged_pr = {'number': 17, 'head': {'sha': HEAD}, 'merged': True,
                     'merge_commit_sha': 'd' * 40}
        saved = []
        git_calls = []
        with patch.object(task_advance, '_d6_snapshot', return_value=ready_d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=merged_pr), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(
                task_id=record['task_id'],
                store=store,
                pr_number=17,
                github_get=lambda _path: [],
                record_github_event_call=lambda *args: saved.append(args),
                publish_git_runner=lambda _root, args: git_calls.append(list(args)),
            )
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(git_calls, [])
        self.assertEqual(len(saved), 1)

    def test_ready_pr_behind_or_diverged_with_clean_merge_tree_calls_publish_once_and_resyncs(self):
        for status in ('behind', 'diverged'):
            with self.subTest(status=status):
                record = ready_record()
                store = ReadyResyncStore(record)
                get, reads = self._github_get(compare_reply={'status': status})
                (state, git_calls, gh_calls, recorded, events,
                 git_runner, gh_runner, record_result) = self._fake_runners(merge_tree_code=0)

                with patch.object(task_advance, '_d6_snapshot', return_value=ready_d6()), \
                     patch.object(task_advance, '_review_outcomes', return_value=[]), \
                     patch.object(task_advance, '_publish_task',
                                  wraps=task_advance._publish_task) as publish_spy:
                    result = task_advance.advance(
                        task_id=record['task_id'],
                        store=store,
                        pr_number=17,
                        github_get=get,
                        publish_git_runner=git_runner,
                        publish_gh_runner=gh_runner,
                        record_result_call=record_result,
                    )

                publish_spy.assert_called_once()
                self.assertEqual(result['step'], 'RESYNCED_WITH_MAIN')
                self.assertNotEqual(result['step'], 'READY_FOR_INTEGRATION')
                self.assertEqual(result['next'], 'RESYNCED_WITH_MAIN')
                self.assertEqual(result['codes'], ['READY_RESYNCED'])
                self.assertEqual(result['head'], NEW_HEAD)
                self.assertEqual(result['pr'], 17)
                self.assertEqual(result['result'], {'pr': 17, 'head': NEW_HEAD})
                self.assertEqual(state['head'], NEW_HEAD)
                self.assertEqual(git_calls[:2], [
                    ['fetch', 'origin', 'main'],
                    ['merge-tree', '--write-tree', 'origin/main', 'HEAD'],
                ])
                self.assertEqual(len(recorded), 1)
                self.assertEqual(recorded[0][1], NEW_HEAD)
                self.assertLess(events.index(('merge', NEW_HEAD)),
                                events.index(('record', NEW_HEAD)))
                self.assertLess(events.index(('record', NEW_HEAD)),
                                events.index(('push', NEW_HEAD)))
                self.assertTrue(gh_calls)
                self.assertIn(f'compare/{MAIN_SHA}...{HEAD}', reads)

    def test_behind_main_with_merge_tree_conflict_refuses_without_merge_push_or_record(self):
        for status in ('behind', 'diverged'):
            with self.subTest(status=status):
                record = ready_record()
                store = ReadyResyncStore(record)
                get, _reads = self._github_get(compare_reply={'status': status})
                (state, git_calls, gh_calls, recorded, events,
                 git_runner, gh_runner, record_result) = self._fake_runners(merge_tree_code=1)

                with patch.object(task_advance, '_d6_snapshot', return_value=ready_d6()), \
                     patch.object(task_advance, '_review_outcomes', return_value=[]), \
                     patch.object(task_advance, '_publish_task',
                                  wraps=task_advance._publish_task) as publish_spy:
                    result = task_advance.advance(
                        task_id=record['task_id'],
                        store=store,
                        pr_number=17,
                        github_get=get,
                        publish_git_runner=git_runner,
                        publish_gh_runner=gh_runner,
                        record_result_call=record_result,
                    )

                publish_spy.assert_not_called()
                self.assertEqual(result['step'], 'REFUSED')
                self.assertEqual(result['next'], 'REFUSED')
                self.assertEqual(result['codes'], ['READY_RESYNC_CONFLICT'])
                self.assertIsNone(result['result'])
                self.assertEqual(state['head'], HEAD)
                self.assertEqual(git_calls, [
                    ['fetch', 'origin', 'main'],
                    ['merge-tree', '--write-tree', 'origin/main', 'HEAD'],
                ])
                self.assertEqual(recorded, [])
                self.assertEqual(gh_calls, [])
                self.assertFalse(any(item[0] in ('merge', 'push', 'record') for item in events))

    def test_behind_main_with_other_merge_tree_or_fetch_failure_refuses_with_resync_failed(self):
        cases = (
            ('merge-tree exit 2', {'merge_tree_code': 2}),
            ('merge-tree exit 128', {'merge_tree_code': 128}),
            ('merge-tree raises', {'merge_tree_code': OSError('git error')}),
            ('fetch exit 1', {'fetch_code': 1}),
            ('fetch raises', {'fetch_code': OSError('fetch error')}),
        )
        for label, kwargs in cases:
            with self.subTest(label=label):
                record = ready_record()
                store = ReadyResyncStore(record)
                get, _reads = self._github_get(compare_reply={'status': 'behind'})
                (state, _git_calls, gh_calls, recorded, events,
                 git_runner, gh_runner, record_result) = self._fake_runners(**kwargs)

                with patch.object(task_advance, '_d6_snapshot', return_value=ready_d6()), \
                     patch.object(task_advance, '_review_outcomes', return_value=[]), \
                     patch.object(task_advance, '_publish_task',
                                  wraps=task_advance._publish_task) as publish_spy:
                    result = task_advance.advance(
                        task_id=record['task_id'],
                        store=store,
                        pr_number=17,
                        github_get=get,
                        publish_git_runner=git_runner,
                        publish_gh_runner=gh_runner,
                        record_result_call=record_result,
                    )

                publish_spy.assert_not_called()
                self.assertEqual(result['step'], 'REFUSED')
                self.assertEqual(result['next'], 'REFUSED')
                self.assertEqual(result['codes'], ['READY_RESYNC_FAILED'])
                self.assertEqual(state['head'], HEAD)
                self.assertEqual(recorded, [])
                self.assertEqual(gh_calls, [])
                self.assertFalse(any(item[0] in ('merge', 'push', 'record') for item in events))

    def test_compare_read_failure_keeps_ready_behaviour_and_adds_unknown_code_with_no_git_call(self):
        cases = (
            ('branches/main raises', {'main_reply': RuntimeError('offline')}),
            ('branches/main malformed', {'main_reply': {'commit': {'sha': 'bad'}}}),
            ('compare raises', {'compare_reply': RuntimeError('offline')}),
            ('compare malformed', {'compare_reply': []}),
            ('compare unknown status', {'compare_reply': {'status': 'unknown'}}),
        )
        for label, kwargs in cases:
            with self.subTest(label=label):
                record = ready_record()
                store = ReadyResyncStore(record)
                get, _reads = self._github_get(**kwargs)
                git_calls = []

                with patch.object(task_advance, '_d6_snapshot', return_value=ready_d6()), \
                     patch.object(task_advance, '_review_outcomes', return_value=[]):
                    result = task_advance.advance(
                        task_id=record['task_id'],
                        store=store,
                        pr_number=17,
                        github_get=get,
                        publish_git_runner=lambda _root, args: git_calls.append(list(args)),
                    )

                self.assertEqual(result['step'], 'READY_FOR_INTEGRATION')
                self.assertEqual(result['next'], 'READY_FOR_INTEGRATION')
                self.assertIn('WAITING_INTEGRATION', result['codes'])
                self.assertIn('READY_MAIN_STATUS_UNKNOWN', result['codes'])
                self.assertEqual(git_calls, [])

    def test_after_resync_to_new_head_decide_and_advance_require_review_again(self):
        record = ready_record()
        store = ReadyResyncStore(record)
        get, _reads = self._github_get(compare_reply={'status': 'behind'})
        (_state, _git_calls, _gh_calls, _recorded, _events,
         git_runner, gh_runner, record_result) = self._fake_runners(merge_tree_code=0)

        with patch.object(task_advance, '_d6_snapshot', return_value=ready_d6()), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            resynced = task_advance.advance(
                task_id=record['task_id'],
                store=store,
                pr_number=17,
                github_get=get,
                publish_git_runner=git_runner,
                publish_gh_runner=gh_runner,
                record_result_call=record_result,
            )
        self.assertEqual(resynced['step'], 'RESYNCED_WITH_MAIN')
        self.assertEqual(resynced['head'], NEW_HEAD)

        new_d6 = ready_d6(head=NEW_HEAD)
        new_pr = {'number': 17, 'head': {'sha': NEW_HEAD}, 'state': 'open', 'merged': False}
        related = {'events': list(record['completion_events']), 'repair_claims': []}

        step_after_resync = task_advance.decide(record, new_d6, new_pr, related)
        self.assertNotEqual(step_after_resync, 'READY_FOR_INTEGRATION')
        self.assertEqual(step_after_resync, 'RUN_REMOTE_TEST')

        record['remote_test_receipts'] = [remote_receipt(record, source_sha=NEW_HEAD)]
        step_after_remote_test = task_advance.decide(record, new_d6, new_pr, related)
        self.assertNotEqual(step_after_remote_test, 'READY_FOR_INTEGRATION')
        self.assertEqual(step_after_remote_test, 'DISPATCH_REVIEW')

        dispatched = []
        with patch.object(task_advance, '_d6_snapshot', return_value=new_d6), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            review_step = task_advance.advance(
                task_id=record['task_id'],
                store=store,
                pr_number=17,
                github_get=lambda path: green_linux_ci(path, new_pr),
                dispatch_review_call=lambda **kwargs: dispatched.append(kwargs) or {
                    'state': 'DISPATCHED'
                },
            )
        self.assertEqual(review_step['step'], 'DISPATCH_REVIEW')
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]['version'], 2)

    def test_merge_flag_on_behind_pr_still_refuses_as_today(self):
        record = ready_record(record_sha256='r' * 64)
        store = ReadyResyncStore(record)
        pr = {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False,
              'mergeable': True, 'labels': [{'name': 'mac-ci'}]}
        checks = {
            'total_count': 2,
            'check_runs': [
                {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
                 'conclusion': 'success'},
                {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
                 'conclusion': 'success'},
            ],
        }

        def base_get(path):
            if path.startswith('commits/'):
                return checks
            if path.endswith('/files?per_page=100'):
                return [{'filename': 'src/module.py'}]
            if path == 'pulls/17':
                return pr
            return []

        github_get = with_current_main(base_get, status='behind')

        git_calls = []
        gh_calls = []
        with patch.object(task_advance, '_d6_snapshot', return_value=ready_d6()), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(
                task_id=record['task_id'],
                store=store,
                pr_number=17,
                merge=True,
                github_get=github_get,
                publish_git_runner=lambda _root, args: git_calls.append(list(args)),
                merge_gh_runner=lambda _root, args: gh_calls.append(list(args)),
            )
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_PR_BEHIND_MAIN'])
        self.assertEqual(git_calls, [])
        self.assertEqual(gh_calls, [])


if __name__ == '__main__':
    unittest.main()
