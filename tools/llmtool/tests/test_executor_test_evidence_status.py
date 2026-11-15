"""#2119 WP-3 slice 2: executor_test_evidence.remote_evidence_status, pure and not wired."""
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llmtool import executor_test_evidence as ev  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402
from llmtool.task_store import _json_bytes  # noqa: E402

HEAD = 'a' * 40
EXEC = 'd6-task'
TUI = 'test_chat_tui_pty'
AB = ('test_a', 'test_b')


def entry(module, ran, final, skipped=0):
    return dict(module=module, ran=ran, skipped=skipped, final=final)


def sealed(body):
    body = dict(body)
    body['receipt_digest'] = hashlib.sha256(_json_bytes(body)).hexdigest()
    return body


def receipt(result, modules, entries=None, **extra):
    body = dict(task_id='t1', run_id='r1', executor_task=EXEC, source_sha=HEAD, result=result)
    finals = [item['final'] for item in entries] if entries is not None else ['OK'] * len(modules)
    total = sum(item['ran'] for item in entries) if entries is not None else 3
    body.update(modules=list(modules), final=finals, ran_total=total, failures=[])
    if entries is not None:
        body['module_results'] = entries
    body.update(extra)
    return sealed(body)


def status(*receipts, remote=AB, unsupported=()):
    record = dict(task_id='t1', run_id='r1', remote_test_receipts=list(receipts))
    expected = (tuple(sorted(remote)), (), tuple(unsupported))
    return ev.remote_evidence_status(record, executor_task=EXEC, head=HEAD, expected=expected)


def clean():
    return [entry('test_a', 2, 'OK'), entry('test_b', 3, 'OK')]


class RemoteEvidenceStatusTests(unittest.TestCase):
    def test_clean_exact_set_passes_with_its_digest(self):
        good = receipt('PASS', AB, clean())
        self.assertEqual(status(good), (ev.STEP_PASS, None, (), good['receipt_digest']))

    def test_f1_tui_skip_beside_a_normal_module_is_not_executed(self):
        entries = [entry(TUI, 0, 'OK (skipped=1)', 1), entry('test_b', 12, 'OK')]
        got = status(receipt('PASS', (TUI, 'test_b'), entries), remote=(TUI, 'test_b'))
        self.assertEqual(got[:3], (ev.STEP_STOP, ev.MODULE_NOT_EXECUTED, (TUI,)))

    def test_ran_with_skips_is_skipped(self):
        entries = [entry('test_a', 5, 'OK (skipped=2)', 2), entry('test_b', 1, 'OK')]
        got = status(receipt('PASS', AB, entries))
        self.assertEqual(got[:3], (ev.STEP_STOP, ev.MODULE_SKIPPED, ('test_a',)))

    def test_ran_zero_skipped_zero_is_not_executed(self):
        entries = [entry('test_a', 0, 'OK'), entry('test_b', 2, 'OK')]
        got = status(receipt('PASS', AB, entries))
        self.assertEqual(got[:3], (ev.STEP_STOP, ev.MODULE_NOT_EXECUTED, ('test_a',)))

    def test_both_conditions_list_both_modules_as_not_executed(self):
        first = entry('test_a', 0, 'OK (skipped=1)', 1)
        entries = [first, entry('test_b', 4, 'OK (skipped=1)', 1)]
        got = status(receipt('PASS', AB, entries))
        self.assertEqual(got[:3], (ev.STEP_STOP, ev.MODULE_NOT_EXECUTED, AB))

    def test_all_skip_fail_is_not_executed_with_and_without_module_results(self):
        entries = [entry(TUI, 0, 'OK (skipped=1)', 1)]
        legacy = receipt('FAIL', (TUI,), final=['OK (skipped=1)'], ran_total=0)
        for item in (receipt('FAIL', (TUI,), entries), legacy):
            with self.subTest(legacy='module_results' not in item):
                got = status(item, remote=(TUI,))
                self.assertEqual(got[:3], (ev.STEP_STOP, ev.MODULE_NOT_EXECUTED, (TUI,)))

    def test_fail_with_a_listed_module_is_test_failed(self):
        entries = [entry('test_a', 3, 'FAILED (failures=1)')]
        failed = receipt('FAIL', ('test_a',), entries, failures=['test_a'])
        got = status(failed, remote=('test_a',))
        self.assertEqual(got[:3], (ev.STEP_REPAIR, ev.TEST_FAILED, ('test_a',)))

    def test_setup_failure_with_fewer_finals_is_test_failed(self):
        got = status(receipt('FAIL', AB, [entry('test_a', 3, 'OK')]))
        self.assertEqual(got[:3], (ev.STEP_REPAIR, ev.TEST_FAILED, ()))

    def test_subset_or_superset_is_a_mismatch(self):
        good = receipt('PASS', AB, clean())
        for remote in (('test_a',), ('test_a', 'test_b', 'test_c')):
            with self.subTest(remote=remote):
                got = status(good, remote=remote)
                self.assertEqual(got[:2], (ev.STEP_RUN, ev.MODULES_MISMATCH))

    def test_older_head_and_other_bindings_are_missing(self):
        changes = (dict(source_sha='b' * 40), dict(task_id='t2'), dict(run_id='r2'))
        for change in changes + (dict(executor_task='other'),):
            with self.subTest(change=sorted(change)):
                got = status(receipt('PASS', AB, clean(), **change))
                self.assertEqual(got, (ev.STEP_RUN, ev.RECEIPT_MISSING, (), None))

    def test_edited_field_is_invalid(self):
        edited = receipt('PASS', AB, clean())
        edited['ran_total'] = 99
        self.assertEqual(status(edited), (ev.STEP_STOP, ev.EVIDENCE_INVALID, (), None))

    def test_legacy_pass_asks_for_a_fresh_run(self):
        self.assertEqual(status(receipt('PASS', AB))[:2], (ev.STEP_RUN, ev.EVIDENCE_LEGACY))

    def test_other_results_never_pass(self):
        for result in ('TIMEOUT', 'SHA_MISMATCH', 'ADMISSION_DENIED'):
            with self.subTest(result=result):
                self.assertNotEqual(status(receipt(result, AB, clean()))[0], ev.STEP_PASS)
        timeout = status(receipt('TIMEOUT', AB, clean()))
        self.assertEqual(timeout[:2], (ev.STEP_ATTENTION, 'REMOTE_TEST_TIMEOUT'))

    def test_unsupported_paths_stop_before_any_receipt(self):
        nine = ['tools/llmtool/tests/test_m%d.py' % index for index in range(9)]
        cases = (['tools/llmtool/scripts/test_x.py'], ['tools/llmtool/tests/test_Upper.py'], nine)
        for paths in cases:
            with self.subTest(count=len(paths)):
                remote, tui, unsupported = ev.expected_remote_modules(paths, [])
                got = status(remote=remote, unsupported=unsupported)
                self.assertEqual(got[:2], (ev.STEP_STOP, ev.MODULE_UNSUPPORTED))

    def test_tui_paths_are_classified(self):
        path = 'tools/llmtool/tests/test_chat_tui_pty.py'
        remote, tui, _unsupported = ev.expected_remote_modules([path], [])
        self.assertEqual((remote, tui), ((TUI,), (TUI,)))

    def test_bad_arguments_raise(self):
        record = dict(task_id='t1', run_id='r1', remote_test_receipts=[])
        with self.assertRaises(LLMToolError):
            ev.remote_evidence_status(record, executor_task=EXEC, head=HEAD, expected=())
        with self.assertRaises(LLMToolError):
            ev.remote_evidence_status([], executor_task=EXEC, head=HEAD, expected=((), (), ()))

    def test_other_legacy_fail_asks_for_a_fresh_run(self):
        legacy = receipt('FAIL', AB, failures=['test_a'])
        self.assertEqual(status(legacy)[:3], (ev.STEP_RUN, ev.EVIDENCE_LEGACY, ()))

    def test_long_final_in_module_results_is_invalid_not_raised(self):
        final = 'FAILED (skipped=' + '9' * 5000 + ')'
        entries = [entry('test_a', 1, final), entry('test_b', 1, 'OK')]
        got = status(receipt('FAIL', AB, entries, failures=['test_a']))
        self.assertEqual(got[:2], (ev.STEP_STOP, ev.EVIDENCE_INVALID))


if __name__ == '__main__':
    unittest.main()
