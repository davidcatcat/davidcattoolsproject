import hashlib
import unittest
from unittest.mock import patch

from llmtool import task_advance, task_submission_cli
from llmtool.task_store import _json_bytes
from test_task_advance import HEAD, PR, base_record, d6, remote_receipt


def check_run(run_id=1, *, head=HEAD, name='check (Linux 3.14)',
              status='completed', conclusion='success'):
    return {'id': run_id, 'head_sha': head, 'name': name, 'status': status,
            'conclusion': conclusion}


class Store:
    def __init__(self, record):
        self.record = record

    def get(self, _task_id):
        return self.record


class TaskAdvanceCiGateTests(unittest.TestCase):
    def advance_with_ci(self, reply, *, pr=PR):
        record = base_record()
        store = Store(record)
        calls = []

        def github_get(path):
            calls.append(path)
            if path.startswith('commits/'):
                if isinstance(reply, Exception):
                    raise reply
                return reply[0]
            if path.endswith('/files?per_page=100'):
                return reply[1]
            return pr

        dispatched = []
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, github_get=github_get,
                dispatch_review_call=lambda **kw: dispatched.append(kw) or {'sent': True})
        return result, dispatched, calls

    def test_success_dispatches_reviewer(self):
        result, dispatched, calls = self.advance_with_ci(
            ({'total_count': 1, 'check_runs': [check_run()]},
             [{'filename': 'src/app.py'}]))
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(len(dispatched), 1)
        checks_read = f'commits/{HEAD}/check-runs?per_page=100'
        files_read = 'pulls/17/files?per_page=100'
        self.assertIn(checks_read, calls)
        self.assertIn(files_read, calls)
        self.assertLess(calls.index(checks_read), calls.index(files_read))

    def test_missing_run_waits_without_dispatch(self):
        result, dispatched, _ = self.advance_with_ci(
            ({'total_count': 0, 'check_runs': []}, [{'filename': 'src/app.py'}]))
        self.assertEqual((result['step'], result['codes']),
                         ('WAITING_CI', ['LINUX_CI_PENDING']))
        self.assertEqual(dispatched, [])

    def test_incomplete_run_waits_without_dispatch(self):
        result, dispatched, _ = self.advance_with_ci(
            ({'total_count': 1, 'check_runs': [check_run(status='in_progress',
                                                         conclusion=None)]},
             [{'filename': 'src/app.py'}]))
        self.assertEqual((result['step'], result['codes']),
                         ('WAITING_CI', ['LINUX_CI_PENDING']))
        self.assertEqual(dispatched, [])

    def test_non_success_conclusion_fails_without_dispatch(self):
        result, dispatched, _ = self.advance_with_ci(
            ({'total_count': 1, 'check_runs': [check_run(conclusion='failure')]},
             [{'filename': 'src/app.py'}]))
        self.assertEqual((result['step'], result['codes']),
                         ('CI_FAILED', ['LINUX_CI_FAILED']))
        self.assertEqual(dispatched, [])

    def test_read_failure_and_malformed_reply_wait_without_dispatch(self):
        for reply in (RuntimeError('unavailable'), ({'check_runs': []}, [])):
            with self.subTest(reply=reply):
                result, dispatched, _ = self.advance_with_ci(reply)
                self.assertEqual((result['step'], result['codes']),
                                 ('WAITING_CI', ['LINUX_CI_UNAVAILABLE']))
                self.assertEqual(dispatched, [])

    def test_docs_only_selects_docs_check(self):
        result, dispatched, _ = self.advance_with_ci(
            ({'total_count': 1, 'check_runs': [check_run(name='check (docs)')]},
             [{'filename': 'docs/guide.md'}]))
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(len(dispatched), 1)

    def test_stale_head_run_is_ignored(self):
        result, dispatched, _ = self.advance_with_ci(
            ({'total_count': 1, 'check_runs': [check_run(head='b' * 40)]},
             [{'filename': 'src/app.py'}]))
        self.assertEqual((result['step'], result['codes']),
                         ('WAITING_CI', ['LINUX_CI_PENDING']))
        self.assertEqual(dispatched, [])

    def test_highest_id_run_decides(self):
        scenarios = (([check_run(2, conclusion='failure'), check_run(3)],
                      'DISPATCH_REVIEW', []),
                     ([check_run(2), check_run(3, conclusion='failure')],
                      'CI_FAILED', ['LINUX_CI_FAILED']))
        for runs, expected_step, expected_codes in scenarios:
            with self.subTest(runs=runs):
                result, dispatched, _ = self.advance_with_ci(
                    ({'total_count': 2, 'check_runs': runs},
                     [{'filename': 'src/app.py'}]))
                self.assertEqual(result['step'], expected_step)
                self.assertEqual(result['codes'], expected_codes)
                self.assertEqual(len(dispatched), int(expected_step == 'DISPATCH_REVIEW'))

    def test_remote_test_summary_and_pr_comment_exclude_excerpt(self):
        record = base_record(remote_test_outcomes=[])
        store = Store(record)
        receipt = remote_receipt(record, result='FAIL', excerpt='private failure text')
        receipt.update(ran_total=12, elapsed_s=3.5)
        receipt.pop('receipt_digest')
        receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()
        calls = []

        def remote_test(_args, *, store):
            store.record.setdefault('remote_test_receipts', []).append(receipt)
            return 0

        def gh_runner(_root, argv):
            calls.append(argv)
            return type('Response', (), {'returncode': 0})()

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_submission_cli, 'run_task_remote_test', side_effect=remote_test):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, publish_gh_runner=gh_runner)
        summary = result['result']['remote_test']
        self.assertEqual(summary, {'result': 'FAIL', 'modules': ['test_task_advance'],
            'ran_total': 12, 'elapsed_s': 3.5,
            'receipt_digest': receipt['receipt_digest'][:8]})
        self.assertEqual(result['step'], 'RUN_REMOTE_TEST')
        self.assertEqual(calls[0][:3], ['pr', 'comment', '17'])
        body = calls[0][calls[0].index('--body') + 1]
        self.assertTrue(body.startswith(
            '<!-- activity: type=task actor=PM target=PR status=TRACKING -->\n'))
        self.assertIn('EC2 remote-test', body)
        self.assertIn('D6=executor-1', body)
        self.assertNotIn(d6()['root'], body)
        self.assertNotIn('private failure text', body)

    def test_remote_test_comment_failure_adds_only_warning_code(self):
        record = base_record(remote_test_outcomes=[])
        store = Store(record)
        receipt = remote_receipt(record)
        receipt.update(ran_total=1, elapsed_s=1.0)
        receipt.pop('receipt_digest')
        receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()

        def remote_test(_args, *, store):
            store.record.setdefault('remote_test_receipts', []).append(receipt)
            return 0

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_submission_cli, 'run_task_remote_test', side_effect=remote_test):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17,
                publish_gh_runner=lambda *_args: type('Response', (), {'returncode': 1})())
        self.assertEqual(result['codes'], ['REMOTE_TEST_COMMENT_FAILED'])
        self.assertEqual(result['result']['remote_test']['result'], 'PASS')


if __name__ == '__main__':
    unittest.main()
