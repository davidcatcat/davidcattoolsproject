"""#2473: task advance skips the EC2 prerequisite for a documentation-only PR.

One test per row of the case table. GitHub, the D6 snapshot, the EC2 run and
the review dispatch are all injected; no network, Git or EC2 is touched.
"""

import copy
import hashlib
import unittest
from unittest.mock import Mock, patch

from llmtool import task_advance
from llmtool.task_store import _json_bytes


HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
NUMBER = 17
DOCS_CHECK = 'check (docs)'
LINUX_CHECK = 'check (Linux 3.14)'
TASK_ID = '1' * 32


def base_record(**changes):
    record = {
        'task_id': TASK_ID, 'run_id': '2' * 32,
        'developer': {'state': 'DEVELOPER_RESPONDED'},
        'completion': {'executor_task_id': 'executor-1', 'state': 'WAITING_REVIEW',
                       'evidence': {'result': {'result_head': HEAD}}},
        'review_dispatch_claim': [], 'review_repair_claims': [],
        'completion_events': [], 'remote_test_outcomes': [],
    }
    record.update(changes)
    return record


def d6_snapshot():
    return {'root': '/repo/task', 'task_id': 'executor-1', 'result_head': HEAD,
            'recorded_result_head': HEAD, 'registered_result_head': HEAD,
            'dirty': False, 'state': 'CLEAN'}


def pr_snapshot(**changes):
    pr = {'number': NUMBER, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False}
    pr.update(changes)
    return pr


def remote_receipt(record, result, **changes):
    receipt = {
        'task_id': record['task_id'], 'run_id': record['run_id'],
        'executor_task': 'executor-1', 'source_sha': HEAD, 'result': result,
        'modules': ['test_task_advance'], 'ran_total': 1, 'elapsed_s': 0.01,
    }
    if result == 'PASS':
        receipt.update(final=['OK'], failures=[])
    receipt.update(changes)
    receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()
    return receipt


class FakeStore:
    def __init__(self, record):
        self.record = record

    def get(self, task_id):
        return copy.deepcopy(self.record)

    def task_ids(self):
        return [self.record['task_id']]


def check_run(name, conclusion='success', status='completed', run_id=1):
    return {'id': run_id, 'name': name, 'head_sha': HEAD, 'status': status,
            'conclusion': conclusion}


def make_github(files, *, runs, changed_files=None, pr=None, files_error=False,
                second_pr=None, third_pr=None):
    """Return a github_get answering the PR, its files and its check runs."""
    default_count = len(files) if changed_files is None else changed_files
    pr = pr_snapshot() if pr is None else pr
    pr = dict(pr, changed_files=default_count)
    if second_pr is not None and 'changed_files' not in second_pr:
        second_pr = dict(second_pr, changed_files=default_count)
    if third_pr is not None and 'changed_files' not in third_pr:
        third_pr = dict(third_pr, changed_files=default_count)
    pulls_reads = []

    def get(path):
        if path == f'pulls/{NUMBER}':
            pulls_reads.append(path)
            if third_pr is not None and len(pulls_reads) > 2:
                return third_pr
            if second_pr is not None and len(pulls_reads) > 1:
                return second_pr
            return pr
        if path.startswith(f'pulls/{NUMBER}/files?per_page=100'):
            if files_error:
                raise OSError('files unavailable')
            return files
        if path == f'commits/{HEAD}/check-runs?per_page=100':
            return {'total_count': len(runs), 'check_runs': runs}
        return []
    return get


def docs_files():
    return [{'filename': 'docs/guide.md', 'status': 'modified'},
            {'filename': 'tools/llmtool/README.md', 'status': 'modified'}]


class AdvanceDocsOnlyTests(unittest.TestCase):
    def _advance(self, github_get, record=None):
        record = base_record() if record is None else record
        remote = Mock(return_value={})
        dispatch = Mock(return_value={'state': 'DISPATCHED'})
        repair = Mock(return_value={'state': 'DISPATCHED'})
        with patch.object(task_advance, '_d6_snapshot', return_value=d6_snapshot()), \
                patch.object(task_advance, '_run_remote_test', remote):
            result = task_advance.advance(
                task_id=TASK_ID, store=FakeStore(record), pr_number=NUMBER,
                github_get=github_get, dispatch_review_call=dispatch,
                repair_review_call=repair)
        return result, remote, dispatch

    def test_a_docs_only_skips_ec2_and_dispatches_review(self):
        get = make_github(docs_files(), runs=[check_run(DOCS_CHECK)])
        result, remote, dispatch = self._advance(get)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        remote.assert_not_called()
        dispatch.assert_called_once()

    def test_b_docs_only_with_test_modules_in_read_paths(self):
        record = base_record()
        record['developer']['read_paths'] = ['tools/llmtool/tests/test_task_advance.py']
        record['read_paths'] = ['tools/llmtool/tests/test_task_advance.py']
        get = make_github(docs_files(), runs=[check_run(DOCS_CHECK)])
        result, remote, dispatch = self._advance(get, record)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        remote.assert_not_called()
        dispatch.assert_called_once()

    def test_c_docs_check_pending_missing_or_failed_blocks_review(self):
        cases = (
            ([check_run(DOCS_CHECK, conclusion=None, status='in_progress')], 'WAITING_CI'),
            ([], 'WAITING_CI'),
            ([check_run(DOCS_CHECK, conclusion='failure')], 'CI_FAILED'),
        )
        for runs, expected in cases:
            with self.subTest(expected=expected, runs=runs):
                get = make_github(docs_files(), runs=runs)
                result, remote, dispatch = self._advance(get)
                self.assertEqual(result['step'], expected)
                remote.assert_not_called()
                dispatch.assert_not_called()

    def test_d_merged_closed_or_head_changed_keeps_protections(self):
        record = base_record()
        merged = pr_snapshot(state='closed', merged=True)
        get = make_github(docs_files(), runs=[check_run(DOCS_CHECK)], pr=merged)
        result, remote, dispatch = self._advance(get)
        self.assertEqual(result['step'], 'PR_MERGED_NO_REVIEW')
        remote.assert_not_called()
        dispatch.assert_not_called()
        # A closed unmerged PR and a moved head never reach the review gate.
        closed = pr_snapshot(state='closed', merged=False)
        moved = pr_snapshot(head={'sha': OTHER_HEAD})
        for pr in (closed, moved):
            self.assertEqual(
                task_advance.decide(record, d6_snapshot(), pr, {}, docs_only=True),
                'PUBLISH')
        # Closing or merging at the same HEAD between the initial snapshot and
        # the docs-only rechecks must not bypass EC2 or dispatch a Reviewer.
        for label, kwargs in (
            ('closed before files', {'second_pr': closed}),
            ('merged before files', {'second_pr': merged}),
            ('closed after files', {'third_pr': closed}),
            ('merged after files', {'third_pr': merged}),
        ):
            with self.subTest(transition=label):
                get = make_github(docs_files(), runs=[check_run(DOCS_CHECK)], **kwargs)
                result, remote, dispatch = self._advance(get)
                self.assertEqual(result['step'], 'RUN_REMOTE_TEST')
                remote.assert_called_once()
                dispatch.assert_not_called()

    def test_e_docs_plus_python_or_test_change_keeps_ec2(self):
        for extra in ('tools/llmtool/llmtool/task_advance.py',
                      'tools/llmtool/tests/test_task_advance.py'):
            with self.subTest(extra=extra):
                files = docs_files() + [{'filename': extra, 'status': 'modified'}]
                get = make_github(files, runs=[check_run(DOCS_CHECK)])
                result, remote, dispatch = self._advance(get)
                remote.assert_called_once()
                dispatch.assert_not_called()
                self.assertEqual(result['step'], 'RUN_REMOTE_TEST')

    def test_f_monitor_tests_or_other_non_docs_keep_ec2(self):
        for name in ('scripts/test_llmtool_monitor.py', 'scripts/llmtool_monitor.py',
                     '.github/workflows/llmtool-linux-ci.yml', 'setup.cfg'):
            with self.subTest(name=name):
                files = [{'filename': name, 'status': 'modified'}]
                get = make_github(files, runs=[check_run(LINUX_CHECK)])
                result, remote, dispatch = self._advance(get)
                remote.assert_called_once()
                dispatch.assert_not_called()
                self.assertEqual(result['step'], 'RUN_REMOTE_TEST')

    def test_g_unreliable_file_list_fails_closed_to_ec2(self):
        runs = [check_run(DOCS_CHECK)]
        docs = docs_files()
        renamed_from_code = [{'filename': 'docs/moved.md', 'status': 'renamed',
                              'previous_filename': 'src/module.py'}]
        renamed_no_data = [{'filename': 'docs/moved.md', 'status': 'renamed'}]
        missing_status = [{'filename': 'docs/moved.md'}]
        unknown_status = [{'filename': 'docs/moved.md', 'status': 'mystery'}]
        duplicate_paths = [{'filename': 'docs/moved.md', 'status': 'modified'},
                           {'filename': 'docs/moved.md', 'status': 'modified'}]
        cases = {
            'read fails': make_github(docs, runs=runs, files_error=True),
            'short of count': make_github(docs, runs=runs, changed_files=3),
            'no count': make_github(docs, runs=runs, changed_files='2'),
            'empty list': make_github([], runs=runs, changed_files=0),
            'rename from code': make_github(renamed_from_code, runs=runs),
            'rename without data': make_github(renamed_no_data, runs=runs),
            'missing status': make_github(missing_status, runs=runs),
            'unknown status': make_github(unknown_status, runs=runs),
            'duplicate paths': make_github(duplicate_paths, runs=runs, changed_files=2),
            'unknown merged state': make_github(docs, runs=runs,
                                                second_pr=pr_snapshot(merged=None)),
            'head races': make_github(docs, runs=runs,
                                      second_pr=pr_snapshot(head={'sha': OTHER_HEAD})),
            'count races after files': make_github(docs, runs=runs,
                                                   third_pr=pr_snapshot(changed_files=3)),
            'invalid count after files': make_github(docs, runs=runs,
                                                     third_pr=pr_snapshot(changed_files='2')),
        }
        for label, get in cases.items():
            with self.subTest(label=label):
                result, remote, dispatch = self._advance(get)
                remote.assert_called_once()
                dispatch.assert_not_called()
                self.assertEqual(result['step'], 'RUN_REMOTE_TEST')

    def test_g_docs_rename_with_docs_previous_path_is_docs_only(self):
        files = [{'filename': 'docs/new.md', 'status': 'renamed',
                  'previous_filename': 'docs/old.md'}]
        get = make_github(files, runs=[check_run(DOCS_CHECK)])
        result, remote, dispatch = self._advance(get)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        remote.assert_not_called()

    def test_i_docs_only_ignores_ec2_fail_timeout_and_sha_mismatch(self):
        record = base_record()
        receipts = {
            'FAIL': remote_receipt(record, 'FAIL', failure_excerpt='one failure',
                                   failures=['x']),
            'SHA_MISMATCH': remote_receipt(record, 'SHA_MISMATCH'),
            'TIMEOUT': remote_receipt(record, 'TIMEOUT'),
        }
        for label, receipt in receipts.items():
            with self.subTest(label=label):
                case = base_record(remote_test_receipts=[receipt])
                get = make_github(docs_files(), runs=[check_run(DOCS_CHECK)])
                result, remote, dispatch = self._advance(get, case)
                self.assertEqual(result['step'], 'DISPATCH_REVIEW')
                remote.assert_not_called()
                dispatch.assert_called_once()
        # Repeated TIMEOUT also no longer needs attention for a docs-only PR.
        twice = base_record(remote_test_receipts=[
            remote_receipt(record, 'TIMEOUT'),
            remote_receipt(record, 'TIMEOUT', elapsed_s=0.02)])
        get = make_github(docs_files(), runs=[check_run(DOCS_CHECK)])
        result, remote, dispatch = self._advance(get, twice)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        remote.assert_not_called()

    def test_j_non_docs_with_ec2_fail_or_mismatch_is_unchanged(self):
        record = base_record()
        files = docs_files() + [{'filename': 'tools/llmtool/llmtool/task_advance.py',
                                 'status': 'modified'}]
        cases = {
            'FAIL': (remote_receipt(record, 'FAIL', failure_excerpt='one failure',
                                    failures=['x']), 'REPAIR_REVIEW'),
            'SHA_MISMATCH': (remote_receipt(record, 'SHA_MISMATCH'), 'NEEDS_ATTENTION'),
        }
        for label, (receipt, expected) in cases.items():
            with self.subTest(label=label):
                case = base_record(remote_test_receipts=[receipt])
                get = make_github(files, runs=[check_run(DOCS_CHECK)])
                result, remote, dispatch = self._advance(get, case)
                self.assertEqual(result['step'], expected)
                remote.assert_not_called()
                dispatch.assert_not_called()

    def test_h_non_docs_ec2_outcomes_are_unchanged(self):
        record = base_record()
        pr = pr_snapshot()
        cases = {
            'none': ({}, 'RUN_REMOTE_TEST'),
            'PASS': ({'remote_test_receipts': [remote_receipt(record, 'PASS')]},
                     'DISPATCH_REVIEW'),
            'FAIL': ({'remote_test_receipts': [remote_receipt(
                record, 'FAIL', failure_excerpt='one failure', failures=['x'])]},
                'REPAIR_REVIEW'),
            'TIMEOUT': ({'remote_test_receipts': [remote_receipt(record, 'TIMEOUT')]},
                        'RUN_REMOTE_TEST'),
            'ADMISSION_DENIED': ({'remote_test_outcomes': [{
                'result': 'ADMISSION_DENIED', 'code': 'MODULE_NOT_ADMITTED',
                'executor_task': 'executor-1', 'sha': HEAD, 'modules': []}]},
                'DISPATCH_REVIEW'),
        }
        for label, (changes, expected) in cases.items():
            with self.subTest(label=label):
                case_record = base_record(**changes)
                self.assertEqual(
                    task_advance.decide(case_record, d6_snapshot(), pr, {}), expected)


if __name__ == '__main__':
    unittest.main()
