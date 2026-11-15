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


if __name__ == '__main__':
    unittest.main()
