"""#2124: task_advance checks ci_failure_overlap before opening a counted CI repair."""

import copy
import hashlib
import unittest
from unittest.mock import Mock, patch

from llmtool import task_advance
from llmtool import task_drive
from llmtool.task_store import _json_bytes


HEAD = 'a' * 40
NUMBER = 2120
TASK_ID = '1' * 32
LINUX_CHECK = 'check (Linux 3.14)'


def _remote_pass_receipt(record, modules):
    receipt = {
        'task_id': record['task_id'],
        'run_id': record['run_id'],
        'executor_task': 'executor-1',
        'source_sha': HEAD,
        'result': 'PASS',
        'modules': list(modules),
        'ran_total': 1,
        'elapsed_s': 0.01,
        'final': ['OK'],
        'failures': [],
    }
    receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()
    return receipt


def _base_record(*, modules=('test_task_store',), claims=None, pm_assignment=None):
    record = {
        'task_id': TASK_ID,
        'run_id': '2' * 32,
        'developer': {'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'custom'},
        'completion': {
            'executor_task_id': 'executor-1',
            'state': 'WAITING_REVIEW',
            'evidence': {'result': {'result_head': HEAD}},
        },
        'review_dispatch_claim': [],
        'review_repair_claims': list(claims or []),
        'completion_events': [],
        'remote_test_outcomes': [],
    }
    if pm_assignment is not None:
        record['pm_assignment'] = pm_assignment
    record['remote_test_receipts'] = [_remote_pass_receipt(record, modules)]
    return record


def _d6_snapshot():
    return {
        'root': '/repo/task',
        'task_id': 'executor-1',
        'result_head': HEAD,
        'recorded_result_head': HEAD,
        'registered_result_head': HEAD,
        'dirty': False,
        'state': 'CLEAN',
    }


class _FakeStore:
    def __init__(self, record):
        self.record = record

    def get(self, _task_id):
        return copy.deepcopy(self.record)

    def task_ids(self):
        return [self.record['task_id']]


def _make_github(files, check_run, *, fail_second_files_read=False):
    files_reads = 0

    def get(path):
        nonlocal files_reads
        if path == f'pulls/{NUMBER}':
            return {
                'number': NUMBER,
                'head': {'sha': HEAD},
                'state': 'open',
                'merged': False,
                'changed_files': len(files),
            }
        if path == f'pulls/{NUMBER}/files?per_page=100':
            files_reads += 1
            if fail_second_files_read and files_reads >= 2:
                raise OSError('files temporarily unreadable')
            return files
        if path == f'commits/{HEAD}/check-runs?per_page=100':
            return {'total_count': 1, 'check_runs': [check_run]}
        if path == f"check-runs/{check_run['id']}":
            return check_run
        return []

    return get


def _failed_run(summary, *, title='Linux CI failure', run_id=77):
    return {
        'id': run_id,
        'name': LINUX_CHECK,
        'head_sha': HEAD,
        'status': 'completed',
        'conclusion': 'failure',
        'output': {'title': title, 'summary': summary},
    }


class CiRepairOverlapAdvanceTests(unittest.TestCase):
    def _advance(self, record, github_get):
        repair = Mock(return_value={'repair_task_id': '3' * 32, 'state': 'DISPATCHED'})
        dispatch = Mock(return_value={'state': 'DISPATCHED'})
        with patch.object(task_advance, '_d6_snapshot', return_value=_d6_snapshot()):
            result = task_advance.advance(
                task_id=TASK_ID,
                store=_FakeStore(record),
                pr_number=NUMBER,
                github_get=github_get,
                dispatch_review_call=dispatch,
                repair_review_call=repair,
            )
        return result, repair, dispatch

    def test_no_overlap_opens_no_counted_repair_and_waits_without_looping(self):
        files = [
            {'filename': 'tools/llmtool/llmtool/task_store.py', 'status': 'modified'},
            {'filename': 'tools/llmtool/tests/test_task_store.py', 'status': 'modified'},
        ]
        run = _failed_run('FAIL: test_unrelated (test_llmtool_monitor.MonitorTests)\nAssertionError')
        result, repair, dispatch = self._advance(_base_record(), _make_github(files, run))

        self.assertEqual(result['step'], 'WAITING_ENV')
        self.assertEqual(result['codes'], ['CI_FAILURE_OUTSIDE_PR_SCOPE'])
        self.assertIn('outside the PR', result['result']['message'])
        self.assertEqual(result['result']['failing_modules'], ['test_llmtool_monitor'])
        repair.assert_not_called()
        dispatch.assert_not_called()

        # task_drive stops immediately on WAITING_ENV without polling or looping.
        with patch('llmtool.source_freshness.code_tree', return_value='d' * 64):
            driven = task_drive.drive(
                task_id=TASK_ID,
                max_steps=5,
                poll_seconds=0,
                advance_call=lambda **_kw: result,
            )
        self.assertEqual(driven['step'], 'WAITING_ENV')
        self.assertEqual(driven['steps'], 1)

    def test_no_overlap_does_not_hit_repair_limit_when_repairs_used_up(self):
        used_claims = [
            {
                'executor_task': 'executor-1',
                'pr': NUMBER,
                'head_sha': 'b' * 40,
                'version': n,
                'attempt': n,
                'family': f'pr-{NUMBER}-ec2',
                'state': 'FAILED',
                'repair_task_id': f'{n:032x}',
                'comment_id': None,
                'packet_sha256': 'f' * 64,
                'claimed_at': f'2026-11-13T0{n}:00:00Z',
            }
            for n in range(1, 6)
        ]
        files = [{'filename': 'tools/llmtool/llmtool/task_store.py', 'status': 'modified'}]
        run = _failed_run('File "scripts/test_llmtool_monitor.py", line 10\nAssertionError')
        result, repair, _dispatch = self._advance(
            _base_record(claims=used_claims), _make_github(files, run))

        self.assertEqual(result['step'], 'WAITING_ENV')
        self.assertEqual(result['codes'], ['CI_FAILURE_OUTSIDE_PR_SCOPE'])
        repair.assert_not_called()

    def test_overlap_with_changed_file_opens_counted_repair(self):
        files = [{'filename': 'tools/llmtool/llmtool/task_store.py', 'status': 'modified'}]
        run = _failed_run('FAIL: test_save (test_task_store.TaskStoreTests)\nAssertionError')
        result, repair, dispatch = self._advance(_base_record(), _make_github(files, run))

        self.assertEqual(result['step'], 'CI_FAILED')
        self.assertEqual(result['codes'], [])
        repair.assert_called_once()
        dispatch.assert_not_called()

    def test_overlap_with_declared_module_opens_counted_repair(self):
        files = [{'filename': 'tools/llmtool/llmtool/unrelated_helper.py', 'status': 'modified'}]
        record = _base_record(
            modules=('test_remote_test',),
            pm_assignment={'acceptance': ['Verify test_remote_test passes.']},
        )
        run = _failed_run('FAIL: test_case (test_remote_test.RemoteTestTests)\nAssertionError')
        result, repair, dispatch = self._advance(record, _make_github(files, run))

        self.assertEqual(result['step'], 'CI_FAILED')
        self.assertEqual(result['codes'], [])
        repair.assert_called_once()
        dispatch.assert_not_called()

    def test_unknown_unparseable_failing_modules_keeps_current_repair_flow(self):
        files = [{'filename': 'tools/llmtool/llmtool/task_store.py', 'status': 'modified'}]
        run = _failed_run('FAIL: test_example\nAssertionError')
        result, repair, dispatch = self._advance(_base_record(), _make_github(files, run))

        self.assertEqual(result['step'], 'CI_FAILED')
        self.assertEqual(result['codes'], [])
        repair.assert_called_once()
        dispatch.assert_not_called()

    def test_unknown_unreadable_pr_files_keeps_current_repair_flow(self):
        files = [{'filename': 'tools/llmtool/llmtool/task_store.py', 'status': 'modified'}]
        run = _failed_run('FAIL: test_unrelated (test_llmtool_monitor.MonitorTests)\nAssertionError')
        github_get = _make_github(files, run, fail_second_files_read=True)
        result, repair, dispatch = self._advance(_base_record(), github_get)

        self.assertEqual(result['step'], 'CI_FAILED')
        self.assertEqual(result['codes'], [])
        repair.assert_called_once()
        dispatch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
