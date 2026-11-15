"""#2119 design PR 1 store part: optional ``module_results`` on remote test receipts.

TaskStore accepts the field with its own structural check and keeps checking
receipts without it exactly as before. The append tests use a real temporary
TaskStore with a created Task, built the same way as
test_task_store_hosted_receipt.py. Every value here is a fixed, non-secret
fake (common-core.md section 4); digests are computed at run time.
"""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from llmtool import task_store
from llmtool.task_submission_cli import _record as _task_record

import review_trust_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


TASK_ID = '1' * 32
RUN_ID = '2' * 32
PM_SESSION = 'pm-module-results-test'
SOURCE_SHA = 'a' * 40


def _seal(receipt):
    """Recompute receipt_digest so only the shape under test can refuse it."""
    receipt = dict(receipt)
    receipt.pop('receipt_digest', None)
    receipt['receipt_digest'] = hashlib.sha256(
        task_store._json_bytes(receipt)).hexdigest()
    return receipt


def _module_results():
    return [
        {'module': 'test_alpha', 'ran': 4, 'skipped': 0, 'final': 'OK'},
        {'module': 'test_beta', 'ran': 3, 'skipped': 0, 'final': 'OK'},
    ]


def _actions():
    return {
        'run_id': 11, 'run_attempt': 1, 'job_id': 12, 'check_run_id': 13,
        'workflow_path': '.github/workflows/llmtool-remote-test.yml',
        'workflow_sha': 'c' * 40, 'labels': ['ubuntu-24.04'],
        'runner_group': 'GitHub Actions', 'nonce': 'd' * 32,
    }


def _receipt(*, module_results=True, excerpt=False, actions=False, task=None):
    """One sealed receipt; ``task`` binds it to a created Task's identifiers."""
    task_id, run_id, pm_session = TASK_ID, RUN_ID, PM_SESSION
    if task is not None:
        task_id, run_id, pm_session = (
            task['task_id'], task['run_id'], task['pm_session_id'])
    receipt = {
        'result': 'PASS', 'exit_code': 0, 'source_sha': SOURCE_SHA,
        'tree': 'b' * 40, 'fetch_head': SOURCE_SHA,
        'modules': ['test_alpha', 'test_beta'], 'identity': {'uid': 1000},
        'started_at': '2026-11-16T00:00:00Z', 'ended_at': '2026-11-16T00:00:05Z',
        'elapsed_s': 5.0, 'ran_total': 7, 'final': ['OK', 'OK'], 'failures': [],
        'log_bytes': 128,
        'log_sha256': hashlib.sha256(b'synthetic remote test log').hexdigest(),
        'remote_log_path': None, 'task_id': task_id, 'run_id': run_id,
        'executor_task': 'rtshape-module-results', 'pm_session': pm_session,
        'command_digest': hashlib.sha256(b'synthetic remote test command').hexdigest(),
    }
    if excerpt:
        # failure_excerpt is refused on PASS, so the excerpt shapes are FAIL receipts.
        receipt.update(result='FAIL', exit_code=1, failures=['test_beta'],
                       final=['OK', 'FAILED (failures=1)'],
                       failure_excerpt='AssertionError: synthetic failure')
    if actions:
        receipt['actions'] = _actions()
    if module_results:
        results = _module_results()
        if excerpt:
            results[1]['final'] = 'FAILED (failures=1)'
        receipt['module_results'] = results
    return _seal(receipt)


def _record(receipt):
    return {'task_id': TASK_ID, 'run_id': RUN_ID, 'pm_session_id': PM_SESSION,
            'remote_test_receipts': [receipt]}


def _bad_module_results():
    good = _module_results()[0]

    def entry(**changes):
        item = dict(good)
        item.update(changes)
        return [item]

    missing_final = dict(good)
    del missing_final['final']
    return {
        'empty list': [],
        'not a list': dict(good),
        'none': None,
        'entry not a dict': ['test_alpha'],
        'missing key': [missing_final],
        'extra key': entry(note='extra'),
        'empty module': entry(module=''),
        'module not str': entry(module=5),
        'negative ran': entry(ran=-1),
        'bool ran': entry(ran=True),
        'float ran': entry(ran=1.0),
        'str ran': entry(ran='1'),
        'negative skipped': entry(skipped=-1),
        'bool skipped': entry(skipped=False),
        'none skipped': entry(skipped=None),
        'final not str': entry(final=None),
        'final list': entry(final=['OK']),
        'one bad entry after a good one': [dict(good), {**good, 'ran': -1}],
    }


class ModuleResultsStructureTest(unittest.TestCase):
    def test_valid_list(self):
        self.assertIs(task_store._valid_remote_test_module_results(_module_results()), True)

    def test_zero_counts_stay_structurally_valid(self):
        # A class- or module-level SkipTest gives ran=0, skipped=1; the store
        # keeps it so the later gate can name the real cause (design 1.4, 3).
        value = [{'module': 'test_alpha', 'ran': 0, 'skipped': 1,
                  'final': 'OK (skipped=1)'}]
        self.assertIs(task_store._valid_remote_test_module_results(value), True)

    def test_each_bad_shape_is_refused(self):
        for label, value in _bad_module_results().items():
            with self.subTest(label):
                self.assertIs(task_store._valid_remote_test_module_results(value), False)


class RecordValidationTest(unittest.TestCase):
    def assert_invalid(self, record):
        with self.assertRaises(task_store.LLMToolError) as caught:
            task_store._validate_remote_test_receipts(record)
        self.assertEqual(caught.exception.task_store_error_code,
                         'REMOTE_TEST_RECEIPT_INVALID')

    def test_receipt_with_module_results_round_trips(self):
        receipt = _receipt()
        stored = json.loads(task_store._json_bytes(_record(receipt)))
        task_store._validate_remote_test_receipts(stored)
        self.assertEqual(stored['remote_test_receipts'][0], receipt)
        self.assertEqual(stored['remote_test_receipts'][0]['module_results'],
                         _module_results())

    def test_every_shape_with_and_without_module_results(self):
        for excerpt in (False, True):
            for actions in (False, True):
                for module_results in (False, True):
                    with self.subTest(excerpt=excerpt, actions=actions,
                                      module_results=module_results):
                        receipt = _receipt(module_results=module_results,
                                           excerpt=excerpt, actions=actions)
                        self.assertIn(set(receipt), task_store._REMOTE_TEST_RECEIPT_SHAPES)
                        task_store._validate_remote_test_receipts(_record(receipt))

    def test_legacy_receipt_without_module_results_is_accepted(self):
        receipt = _receipt(module_results=False)
        self.assertNotIn('module_results', receipt)
        task_store._validate_remote_test_receipts(_record(receipt))

    def test_each_bad_entry_shape_is_refused(self):
        for label, value in _bad_module_results().items():
            with self.subTest(label):
                receipt = _receipt()
                receipt['module_results'] = value
                self.assert_invalid(_record(_seal(receipt)))

    def test_unknown_extra_top_level_field_is_refused(self):
        for module_results in (False, True):
            with self.subTest(module_results=module_results):
                receipt = _receipt(module_results=module_results)
                receipt['extra_field'] = 'extra'
                self.assert_invalid(_record(_seal(receipt)))

    def test_edited_module_results_breaks_the_receipt_digest(self):
        receipt = _receipt()
        receipt['module_results'] = copy.deepcopy(receipt['module_results'])
        receipt['module_results'][0]['ran'] = 5
        self.assert_invalid(_record(receipt))


class AppendRemoteTestReceiptTest(unittest.TestCase):
    """append_remote_test_receipt against a real temporary TaskStore.

    setUp creates one valid Task the same way test_task_store_hosted_receipt.py
    does, so an accepted receipt is written through the store's full record
    validation and read back from disk.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'tasks'
        self.store = task_store.TaskStore(self.root)
        self.task = self._task()
        self.store.create(self.task)
        self.task_id = self.task['task_id']

    @staticmethod
    def _task():
        request = 'Bounded non-sensitive module results task.'
        task_id, run_id = task_store.new_id(), task_store.new_id()
        request_sha = hashlib.sha256(request.encode()).hexdigest()
        task = _task_record(
            task_id, run_id, 'pm-' + task_store.new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED', request_sha=request_sha)
        task.update(
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            response='PM returned a bounded task.', error_code=None,
            pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                           'goal': 'Produce a proposal diff.',
                           'acceptance': ['the proposal is recorded']},
        )
        return task

    def receipt(self, **kwargs):
        return _receipt(task=self.task, **kwargs)

    def reread(self):
        """Read the Task back through a fresh store on the same root."""
        return task_store.TaskStore(self.root).get(self.task_id)

    def assert_appended(self, receipt, expected_receipts):
        self.assertEqual(
            self.store.append_remote_test_receipt(self.task_id, receipt), receipt)
        saved = self.store.get(self.task_id)
        self.assertEqual(saved['remote_test_receipts'], expected_receipts)
        self.assertEqual(self.reread()['remote_test_receipts'], expected_receipts)
        task_store._validate(saved)
        task_store._validate_remote_test_receipts(saved)
        return saved

    def assert_refused(self, receipt):
        before = self.store.get(self.task_id)
        with self.assertRaises(task_store.LLMToolError) as caught:
            self.store.append_remote_test_receipt(self.task_id, receipt)
        self.assertNotIsInstance(caught.exception, task_store._TaskNotFound)
        self.assertEqual(caught.exception.task_store_error_code,
                         'REMOTE_TEST_RECEIPT_INVALID')
        self.assertEqual(self.store.get(self.task_id), before)
        self.assertEqual(self.reread(), before)

    def test_receipt_with_module_results_persists_unchanged(self):
        receipt = self.receipt()
        expected = copy.deepcopy(receipt)
        self.assertEqual(self.store.get(self.task_id).get('remote_test_receipts', []), [])
        saved = self.assert_appended(receipt, [expected])
        stored = saved['remote_test_receipts'][0]
        self.assertEqual(stored, expected)
        self.assertEqual(stored['module_results'], _module_results())
        self.assertEqual(self.reread()['remote_test_receipts'][0]['module_results'],
                         _module_results())

    def test_replaying_the_same_receipt_is_idempotent(self):
        receipt = self.receipt()
        self.assert_appended(receipt, [receipt])
        self.assertEqual(
            self.store.append_remote_test_receipt(self.task_id, copy.deepcopy(receipt)),
            receipt)
        self.assertEqual(self.reread()['remote_test_receipts'], [receipt])

    def test_every_shape_with_and_without_module_results_persists(self):
        appended = []
        for excerpt in (False, True):
            for actions in (False, True):
                for module_results in (False, True):
                    with self.subTest(excerpt=excerpt, actions=actions,
                                      module_results=module_results):
                        receipt = self.receipt(module_results=module_results,
                                               excerpt=excerpt, actions=actions)
                        appended.append(copy.deepcopy(receipt))
                        self.assert_appended(receipt, appended)

    def test_legacy_receipt_without_module_results_still_appends(self):
        receipt = self.receipt(module_results=False)
        self.assertNotIn('module_results', receipt)
        saved = self.assert_appended(receipt, [receipt])
        self.assertNotIn('module_results', saved['remote_test_receipts'][0])

    def test_each_bad_entry_shape_is_refused_and_the_record_is_unchanged(self):
        # A stored receipt with module_results makes "unchanged" non-trivial.
        self.assert_appended(self.receipt(), [self.receipt()])
        for label, value in _bad_module_results().items():
            with self.subTest(label):
                receipt = self.receipt()
                receipt['module_results'] = value
                self.assert_refused(_seal(receipt))

    def test_unknown_extra_top_level_field_is_refused(self):
        for module_results in (False, True):
            with self.subTest(module_results=module_results):
                receipt = self.receipt(module_results=module_results)
                receipt['extra_field'] = 'extra'
                self.assert_refused(_seal(receipt))

    def test_edited_module_results_breaks_the_receipt_digest(self):
        receipt = self.receipt()
        receipt['module_results'] = copy.deepcopy(receipt['module_results'])
        receipt['module_results'][0]['ran'] = 5
        self.assert_refused(receipt)


if __name__ == '__main__':
    unittest.main()
