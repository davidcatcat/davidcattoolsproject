"""#2491: EC2 pre-review gate covers declared and PR-changed test modules.

All GitHub, D6, store, config and SSH transport calls are injected or mocked;
no network, Git or EC2 is touched.
"""

import copy
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from llmtool import remote_test, task_advance
from llmtool.task_store import _json_bytes


HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
TREE = 'c' * 40
LOG_SHA = 'd' * 64
NUMBER = 42
TASK_ID = '1' * 32
RUN_ID = '2' * 32
LINUX_CHECK = 'check (Linux 3.14)'


def base_record(**changes):
    record = {
        'task_id': TASK_ID,
        'run_id': RUN_ID,
        'pm_session_id': 'pm-1',
        'developer': {
            'state': 'DEVELOPER_RESPONDED',
            'read_paths': ['tools/llmtool/tests/test_task_advance.py'],
            'write_paths': ['tools/llmtool/llmtool/task_advance.py'],
        },
        'completion': {
            'executor_task_id': 'executor-1',
            'state': 'WAITING_REVIEW',
            'evidence': {
                'common_dir': '/repo/.git',
                'result': {'result_head': HEAD},
                'review_pr_ci': {'pr_number': NUMBER, 'head_sha': HEAD},
            },
        },
        'review_dispatch_claim': [],
        'review_repair_claims': [],
        'completion_events': [],
        'remote_test_outcomes': [],
        'remote_test_receipts': [],
    }
    record.update(changes)
    return record


def d6_snapshot():
    return {
        'root': '/repo/task',
        'task_id': 'executor-1',
        'result_head': HEAD,
        'recorded_result_head': HEAD,
        'registered_result_head': HEAD,
        'dirty': False,
        'state': 'CLEAN',
    }


def pr_snapshot(**changes):
    pr = {'number': NUMBER, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False}
    pr.update(changes)
    return pr


def remote_receipt(record, modules, result='PASS', **changes):
    receipt = {
        'task_id': record['task_id'],
        'run_id': record['run_id'],
        'executor_task': 'executor-1',
        'source_sha': HEAD,
        'result': result,
        'modules': list(modules),
        'ran_total': len(modules),
        'elapsed_s': 0.01,
    }
    if result == 'PASS':
        receipt.update(
            final=['OK'] * len(modules),
            failures=[],
            module_results=[
                {'module': mod, 'ran': 1, 'skipped': 0, 'final': 'OK'}
                for mod in modules
            ],
        )
    receipt.update(changes)
    receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()
    return receipt


class FakeStore:
    def __init__(self, record):
        self.record = copy.deepcopy(record)
        self.outcomes = []
        self.receipts = []

    def get(self, task_id):
        return copy.deepcopy(self.record)

    def task_ids(self):
        return [self.record['task_id']]

    def record_published_pr(self, task_id, pr, head):
        self.record['published_pr'] = {'pr': pr, 'head_sha': head}

    def append_remote_test_outcome(self, task_id, outcome):
        saved = copy.deepcopy(outcome)
        self.outcomes.append(saved)
        self.record.setdefault('remote_test_outcomes', []).append(saved)
        return {'outcome': saved, 'counter': len(self.outcomes)}

    def append_remote_test_receipt(self, task_id, receipt):
        saved = copy.deepcopy(receipt)
        self.receipts.append(saved)
        self.record.setdefault('remote_test_receipts', []).append(saved)
        return saved


def check_run(name, conclusion='success', status='completed', run_id=1):
    return {
        'id': run_id,
        'name': name,
        'head_sha': HEAD,
        'status': status,
        'conclusion': conclusion,
    }


def make_github(files, *, runs=None, changed_files=None, pr=None, files_error=False,
                second_pr=None, pages=None):
    default_count = len(files) if changed_files is None else changed_files
    pr = pr_snapshot() if pr is None else pr
    pr = dict(pr, changed_files=default_count)
    if second_pr is not None and 'changed_files' not in second_pr:
        second_pr = dict(second_pr, changed_files=default_count)
    runs = [check_run(LINUX_CHECK)] if runs is None else runs
    pulls_reads = []

    def get(path):
        if path == f'pulls/{NUMBER}':
            pulls_reads.append(path)
            if second_pr is not None and len(pulls_reads) > 1:
                return second_pr
            return pr
        if path.startswith(f'pulls/{NUMBER}/files?per_page=100'):
            if files_error:
                raise OSError('files unavailable')
            if pages is not None:
                if '&page=' in path:
                    page_num = int(path.split('&page=')[1])
                else:
                    page_num = 1
                return pages[page_num]
            return files
        if path == f'commits/{HEAD}/check-runs?per_page=100':
            return {'total_count': len(runs), 'check_runs': runs}
        return []

    return get


def _raw_ssh_receipt(modules):
    return json.dumps({
        'result': 'PASS',
        'exit_code': 0,
        'source_sha': HEAD,
        'tree': TREE,
        'fetch_head': HEAD,
        'modules': list(modules),
        'identity': {'uid': 1000},
        'started_at': '2026-11-23T00:00:00Z',
        'ended_at': '2026-11-23T00:00:01Z',
        'elapsed_s': 0.1,
        'ran_total': len(modules),
        'final': ['OK'] * len(modules),
        'failures': [],
        'log_bytes': 10,
        'log_sha256': LOG_SHA,
        'remote_log_path': None,
        'module_results': [
            {'module': mod, 'ran': 1, 'skipped': 0, 'final': 'OK'}
            for mod in modules
        ],
    }, separators=(',', ':'), sort_keys=True).encode('utf-8')


class Ec2ChangedTests2491(unittest.TestCase):
    def _advance(self, github_get, record=None, remote_return=None):
        record = base_record() if record is None else record
        remote = Mock(return_value={} if remote_return is None else remote_return)
        dispatch = Mock(return_value={'state': 'DISPATCHED'})
        repair = Mock(return_value={'state': 'DISPATCHED'})
        with patch.object(task_advance, '_d6_snapshot', return_value=d6_snapshot()), \
                patch.object(task_advance, '_run_remote_test', remote):
            result = task_advance.advance(
                task_id=TASK_ID,
                store=FakeStore(record),
                pr_number=NUMBER,
                github_get=github_get,
                dispatch_review_call=dispatch,
                repair_review_call=repair,
            )
        return result, remote, dispatch

    def _run_remote(self, github_get, *, record=None, modules=(), pr=NUMBER):
        record = base_record() if record is None else record
        store = FakeStore(record)
        transport = Mock(side_effect=lambda argv, script, timeout: SimpleNamespace(
            returncode=0, stdout=_raw_ssh_receipt(argv[12:])))
        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=HEAD, root='/repo/task')), \
                patch.object(remote_test, '_config', return_value={
                    'host': 'ec2.example', 'user': 'runner',
                    'repo_path': '/srv/repo', 'python': '/usr/bin/python3'}), \
                patch.object(remote_test, 'record_remote_test_outcome_event'), \
                patch.object(remote_test, 'record_remote_test_event'):
            res = remote_test.run_remote_test(
                TASK_ID,
                executor_task='executor-1',
                sha=HEAD,
                ref=f'refs/pull/{NUMBER}/head',
                modules=modules,
                pr=pr,
                store=store,
                transport=transport,
                get=github_get,
            )
        return res, transport

    def test_undeclared_changed_test_module_required_and_admitted(self):
        files = [
            {'filename': 'tools/llmtool/llmtool/task_advance.py', 'status': 'modified'},
            {'filename': 'tools/llmtool/tests/test_extra_added.py', 'status': 'added'},
        ]
        # 1. Coverage decision: PASS receipt for declared module alone is rejected.
        only_declared = base_record(
            remote_test_receipts=[remote_receipt(base_record(), ['test_task_advance'])]
        )
        result, remote, dispatch = self._advance(make_github(files), only_declared)
        self.assertEqual(result['step'], 'RUN_REMOTE_TEST')
        remote.assert_called_once()
        dispatch.assert_not_called()

        # 2. Remote-test admission: admits and runs both declared and PR-changed modules.
        res, transport = self._run_remote(make_github(files))
        self.assertEqual(res['result'], 'PASS')
        self.assertEqual(res['modules'], ['test_extra_added', 'test_task_advance'])
        transport.assert_called_once()

        # 3. Explicitly requesting the undeclared changed module is also admitted.
        res_explicit, _ = self._run_remote(
            make_github(files), modules=('test_extra_added',))
        self.assertEqual(res_explicit['result'], 'PASS')
        self.assertEqual(res_explicit['modules'], ['test_extra_added'])

        # 4. PASS receipt covering both declared and PR-changed modules dispatches review.
        both_covered = base_record(
            remote_test_receipts=[
                remote_receipt(base_record(), ['test_extra_added', 'test_task_advance'])
            ]
        )
        result, remote, dispatch = self._advance(make_github(files), both_covered)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        remote.assert_not_called()
        dispatch.assert_called_once()

    def test_unreadable_incomplete_or_head_mismatch_pr_files_fail_closed(self):
        declared_pass = base_record(
            remote_test_receipts=[remote_receipt(base_record(), ['test_task_advance'])]
        )
        docs_file = [{'filename': 'tools/llmtool/llmtool/task_advance.py', 'status': 'modified'}]
        too_many_pages = {p: [{'filename': f'f_{p}_{i}.py', 'status': 'modified'} for i in range(100)]
                          for p in range(1, 31)}
        cases = (
            ('files_unreadable', make_github(docs_file, files_error=True), 'PR_FILES_UNAVAILABLE'),
            ('head_mismatch', make_github(docs_file, second_pr=pr_snapshot(head={'sha': OTHER_HEAD})),
             'PR_FILES_HEAD_MISMATCH'),
            ('too_many_pages', make_github(docs_file, changed_files=3000, pages=too_many_pages),
             'PR_FILES_TOO_MANY'),
            ('count_mismatch', make_github(docs_file, changed_files=2), 'PR_FILES_UNAVAILABLE'),
        )
        for label, github_get, expected_code in cases:
            with self.subTest(label=label):
                # Even with a PASS receipt for declared modules, advance must not dispatch review.
                result, remote, dispatch = self._advance(
                    github_get, declared_pass,
                    remote_return={'result': 'PASS'},
                )
                self.assertEqual(result['step'], 'RUN_REMOTE_TEST')
                self.assertIn(expected_code, result['codes'])
                remote.assert_called_once()
                dispatch.assert_not_called()

                # Remote-test admission itself does not refuse on unreadable PR file data;
                # it admits declared modules only.
                res, transport = self._run_remote(github_get)
                self.assertEqual(res['result'], 'PASS')
                self.assertEqual(res['modules'], ['test_task_advance'])
                transport.assert_called_once()

        # Missing PR number on run_remote_test also admits declared modules only.
        res_no_pr, transport = self._run_remote(make_github(docs_file), pr=None)
        self.assertEqual(res_no_pr['result'], 'PASS')
        self.assertEqual(res_no_pr['modules'], ['test_task_advance'])
        transport.assert_called_once()

    def test_pr_without_test_changes_behaves_as_before(self):
        files = [{'filename': 'tools/llmtool/llmtool/task_advance.py', 'status': 'modified'}]
        declared_pass = base_record(
            remote_test_receipts=[remote_receipt(base_record(), ['test_task_advance'])]
        )
        result, remote, dispatch = self._advance(make_github(files), declared_pass)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        remote.assert_not_called()
        dispatch.assert_called_once()

        # A removed test module is not runnable at HEAD and is not added to required modules.
        removed_test = files + [
            {'filename': 'tools/llmtool/tests/test_removed_old.py', 'status': 'removed'}
        ]
        result, remote, dispatch = self._advance(make_github(removed_test), declared_pass)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        remote.assert_not_called()
        dispatch.assert_called_once()

    def test_darwin_only_changed_test_module_excluded_per_2584(self):
        # Mock _darwin_only_modules returning a Mac-only changed module to verify #2584 rule.
        files = [
            {'filename': 'tools/llmtool/tests/test_mac_only_sample.py', 'status': 'modified'},
        ]
        with patch.object(remote_test, '_darwin_only_modules',
                          return_value=frozenset(('test_mac_only_sample',))):
            required = remote_test.remote_required_modules(base_record(), pr_files=files)
        self.assertEqual(required, ('test_task_advance',))


if __name__ == '__main__':
    unittest.main()
