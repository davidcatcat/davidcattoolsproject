"""#2491: the pre-review EC2 gate covers PR changed test modules.

The coverage decision and remote-test admission use the same module set:
declared Task test modules plus the runnable test modules the PR adds or
changes at its exact head. Doubt about the PR file data fails the gate closed
before any Reviewer is dispatched. GitHub, the D6 snapshot, the EC2 run and
the review dispatch are injected; no network, Git or EC2 is touched.
"""

import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llmtool import remote_test, task_advance
from llmtool.task_store import _json_bytes

try:
    from tests import test_advance_docs_only_2473 as docs
    from tests import test_remote_test as remote_fixtures
except ImportError:
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import test_advance_docs_only_2473 as docs
    import test_remote_test as remote_fixtures


HEAD = docs.HEAD
OTHER_HEAD = docs.OTHER_HEAD
NUMBER = docs.NUMBER


def pass_receipt(record, modules):
    """A minimal PASS receipt the advance status accepts for exactly modules."""
    receipt = {
        'task_id': record['task_id'], 'run_id': record['run_id'],
        'executor_task': 'executor-1', 'source_sha': HEAD, 'result': 'PASS',
        'modules': list(modules), 'ran_total': len(modules), 'elapsed_s': 0.01,
        'final': ['OK'] * len(modules), 'failures': [],
        'module_results': [dict(module=name, ran=1, skipped=0, final='OK')
                           for name in modules],
    }
    receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()
    return receipt


class ChangedModuleCoverageTests(unittest.TestCase):
    def test_required_modules_add_an_undeclared_changed_test_module(self):
        record = docs.base_record()
        record['developer'] = dict(
            record['developer'],
            read_paths=['tools/llmtool/tests/test_declared.py'], write_paths=[])

        self.assertEqual(
            remote_test.remote_required_modules(
                record, changed_modules={'test_changed'}),
            ('test_changed', 'test_declared'))

    def test_pr_changed_module_is_admitted_by_the_remote_run(self):
        class Admission(remote_fixtures.RemoteTestTests):
            pass

        case = Admission(methodName='test_pass_is_stored_on_same_task_and_delivered_to_pm_inbox')
        case.setUp()
        self.addCleanup(case.temp.cleanup)
        case.store.task['developer']['read_paths'] = [
            'tools/llmtool/tests/test_declared.py']
        case.store.task['developer']['write_paths'] = []

        def get(path):
            if path == 'pulls/425':
                return {'number': 425, 'head': {'sha': remote_fixtures.SHA},
                        'changed_files': 1}
            if path == 'pulls/425/files?per_page=100':
                return [{'filename': 'tools/llmtool/tests/test_changed.py',
                         'status': 'added'}]
            raise AssertionError(path)

        payload = json.loads(case.receipt())
        payload.update(
            modules=['test_changed', 'test_declared'],
            final=['OK', 'OK'], ran_total=2,
            module_results=[dict(module='test_changed', ran=1, skipped=0, final='OK'),
                            dict(module='test_declared', ran=1, skipped=0, final='OK')])
        with patch.object(remote_test, '_github_get', side_effect=get):
            result = case.run_it(json.dumps(payload).encode(), modules=(), pr=425)

        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(case.calls[-1][0][-2:],
                         ['test_changed', 'test_declared'])
        self.assertEqual(result['modules'], ['test_changed', 'test_declared'])

    def test_pr_changed_module_is_not_admitted_when_file_data_is_unreadable(self):
        class Admission(remote_fixtures.RemoteTestTests):
            pass

        case = Admission(methodName='test_pass_is_stored_on_same_task_and_delivered_to_pm_inbox')
        case.setUp()
        self.addCleanup(case.temp.cleanup)
        case.store.task['developer']['read_paths'] = [
            'tools/llmtool/tests/test_declared.py']

        def get(path):
            if path == 'pulls/425':
                return {'number': 425, 'head': {'sha': remote_fixtures.SHA},
                        'changed_files': 1}
            if path == 'pulls/425/files?per_page=100':
                raise OSError('files unavailable')
            raise AssertionError(path)

        with patch.object(remote_test, '_github_get', side_effect=get):
            payload = json.loads(case.receipt())
            payload.update(
                modules=['test_declared'], final=['OK'], ran_total=1,
                module_results=[dict(module='test_declared', ran=1, skipped=0,
                                     final='OK')])
            result = case.run_it(json.dumps(payload).encode(), modules=(), pr=425)

        # Admission stays on the declared module and never refuses for doubt.
        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(case.calls[-1][0][-1:], ['test_declared'])


class PreReviewGateTests(unittest.TestCase):
    def advance(self, github_get, record):
        remote = Mock(return_value={})
        dispatch = Mock(return_value={'state': 'DISPATCHED'})
        repair = Mock(return_value={})
        poster = Mock(return_value=SimpleNamespace(returncode=0))
        with patch.object(task_advance, '_d6_snapshot',
                          return_value=docs.d6_snapshot()), \
                patch.object(task_advance, '_run_remote_test', remote):
            result = task_advance.advance(
                task_id=record['task_id'], store=docs.FakeStore(record),
                pr_number=NUMBER, github_get=github_get,
                dispatch_review_call=dispatch, repair_review_call=repair,
                publish_gh_runner=poster)
        return result, remote, dispatch

    @staticmethod
    def record(*modules):
        record = docs.base_record()
        record['developer'] = dict(
            record['developer'],
            read_paths=[f'tools/llmtool/tests/{name}.py' for name in modules],
            write_paths=[])
        return record

    @staticmethod
    def pr_files(files, *, changed_files=None):
        count = len(files) if changed_files is None else changed_files

        def get(path):
            if path == f'pulls/{NUMBER}':
                return dict(docs.pr_snapshot(), changed_files=count)
            if path == f'pulls/{NUMBER}/files?per_page=100':
                return files
            if path == f'commits/{HEAD}/check-runs?per_page=100':
                return {'total_count': 1,
                        'check_runs': [docs.check_run(docs.LINUX_CHECK)]}
            raise AssertionError(path)
        return get

    def test_undeclared_changed_test_module_must_run_before_review(self):
        record = self.record('test_task_advance')
        record['remote_test_receipts'] = [pass_receipt(record, ['test_task_advance'])]
        get = self.pr_files([
            {'filename': 'tools/llmtool/llmtool/task_advance.py', 'status': 'modified'},
            {'filename': 'tools/llmtool/tests/test_changed.py', 'status': 'added'},
        ])

        result, remote, dispatch = self.advance(get, record)

        self.assertEqual(result['step'], 'RUN_REMOTE_TEST')
        remote.assert_called_once()
        dispatch.assert_not_called()

    def test_receipt_covering_the_changed_module_dispatches_review(self):
        record = self.record('test_task_advance')
        record['remote_test_receipts'] = [pass_receipt(
            record, ['test_changed', 'test_task_advance'])]
        get = self.pr_files([
            {'filename': 'tools/llmtool/llmtool/task_advance.py', 'status': 'modified'},
            {'filename': 'tools/llmtool/tests/test_changed.py', 'status': 'added'},
        ])

        result, remote, dispatch = self.advance(get, record)

        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        remote.assert_not_called()
        dispatch.assert_called_once()

    def test_unreadable_pr_files_fail_closed_with_the_reader_code(self):
        record = self.record('test_task_advance')
        record['remote_test_receipts'] = [pass_receipt(record, ['test_task_advance'])]

        def get(path):
            if path == f'pulls/{NUMBER}':
                return dict(docs.pr_snapshot(), changed_files=1)
            if path == f'pulls/{NUMBER}/files?per_page=100':
                raise OSError('files unavailable')
            raise AssertionError(path)

        result, remote, dispatch = self.advance(get, record)

        self.assertEqual(result['step'], 'EC2_GATE_UNSATISFIED')
        self.assertIn('PR_FILES_UNAVAILABLE', result['codes'])
        remote.assert_not_called()
        dispatch.assert_not_called()

    def test_a_head_mismatch_fails_closed_with_the_reader_code(self):
        calls = []

        def get(path):
            if path == f'pulls/{NUMBER}':
                calls.append(path)
                head = HEAD if len(calls) == 1 else OTHER_HEAD
                return dict(docs.pr_snapshot(head={'sha': head}), changed_files=1)
            if path == f'pulls/{NUMBER}/files?per_page=100':
                return [{'filename': 'tools/llmtool/tests/test_changed.py',
                         'status': 'modified'}]
            raise AssertionError(path)

        modules, code = task_advance._pr_changed_test_modules(NUMBER, HEAD, get)

        self.assertEqual(modules, frozenset())
        self.assertEqual(code, 'PR_FILES_HEAD_MISMATCH')

    def test_a_moved_head_dispatches_no_review(self):
        record = self.record('test_task_advance')
        record['remote_test_receipts'] = [pass_receipt(record, ['test_task_advance'])]
        calls = []

        def get(path):
            if path == f'pulls/{NUMBER}':
                calls.append(path)
                head = HEAD if len(calls) == 1 else OTHER_HEAD
                return dict(docs.pr_snapshot(head={'sha': head}), changed_files=1)
            raise AssertionError(path)

        result, remote, dispatch = self.advance(get, record)

        self.assertEqual(result['step'], 'EC2_GATE_UNSATISFIED')
        self.assertIn('PR_FILES_HEAD_MISMATCH', result['codes'])
        remote.assert_not_called()
        dispatch.assert_not_called()

    def test_a_pr_without_test_changes_dispatches_review_as_before(self):
        record = self.record('test_task_advance')
        record['remote_test_receipts'] = [pass_receipt(record, ['test_task_advance'])]
        get = self.pr_files([
            {'filename': 'tools/llmtool/llmtool/task_advance.py', 'status': 'modified'},
        ])

        result, remote, dispatch = self.advance(get, record)

        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        remote.assert_not_called()
        dispatch.assert_called_once()


if __name__ == '__main__':
    unittest.main()
