"""#2119 design PR 1 store part: optional ``module_results`` on remote test receipts.

TaskStore accepts the field with its own structural check and keeps checking
receipts without it exactly as before. Every value here is a fixed, non-secret
fake (common-core.md section 4); digests are computed at run time.
"""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from llmtool import task_store


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


def _receipt(*, module_results=True, excerpt=False, actions=False):
    receipt = {
        'result': 'PASS', 'exit_code': 0, 'source_sha': SOURCE_SHA,
        'tree': 'b' * 40, 'fetch_head': SOURCE_SHA,
        'modules': ['test_alpha', 'test_beta'], 'identity': {'uid': 1000},
        'started_at': '2026-11-16T00:00:00Z', 'ended_at': '2026-11-16T00:00:05Z',
        'elapsed_s': 5.0, 'ran_total': 7, 'final': ['OK', 'OK'], 'failures': [],
        'log_bytes': 128,
        'log_sha256': hashlib.sha256(b'synthetic remote test log').hexdigest(),
        'remote_log_path': None, 'task_id': TASK_ID, 'run_id': RUN_ID,
        'executor_task': 'rtshape-module-results', 'pm_session': PM_SESSION,
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
    """append_remote_test_receipt checks the shape before it reads the Task.

    The store root does not exist, so a receipt that passes the shape check
    reaches the Task read and gets task-not-found, while a refused receipt
    fails REMOTE_TEST_RECEIPT_INVALID first and creates nothing.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'tasks'
        self.store = task_store.TaskStore(self.root)

    def assert_refused(self, receipt):
        with self.assertRaises(task_store.LLMToolError) as caught:
            self.store.append_remote_test_receipt(TASK_ID, receipt)
        self.assertNotIsInstance(caught.exception, task_store._TaskNotFound)
        self.assertEqual(caught.exception.task_store_error_code,
                         'REMOTE_TEST_RECEIPT_INVALID')
        self.assertFalse(self.root.exists())

    def assert_passes_shape_check(self, receipt):
        with self.assertRaises(task_store._TaskNotFound):
            self.store.append_remote_test_receipt(TASK_ID, receipt)
        self.assertFalse(self.root.exists())

    def test_receipt_with_module_results_passes_the_shape_check(self):
        self.assert_passes_shape_check(_receipt())

    def test_every_shape_with_module_results_passes_the_shape_check(self):
        for excerpt in (False, True):
            for actions in (False, True):
                with self.subTest(excerpt=excerpt, actions=actions):
                    self.assert_passes_shape_check(
                        _receipt(excerpt=excerpt, actions=actions))

    def test_legacy_receipt_without_module_results_passes_the_shape_check(self):
        self.assert_passes_shape_check(_receipt(module_results=False))

    def test_each_bad_entry_shape_is_refused(self):
        for label, value in _bad_module_results().items():
            with self.subTest(label):
                receipt = _receipt()
                receipt['module_results'] = value
                self.assert_refused(_seal(receipt))

    def test_unknown_extra_top_level_field_is_refused(self):
        for module_results in (False, True):
            with self.subTest(module_results=module_results):
                receipt = _receipt(module_results=module_results)
                receipt['extra_field'] = 'extra'
                self.assert_refused(_seal(receipt))


if __name__ == '__main__':
    unittest.main()
