"""#2119 WP-1 part (b): _validate_remote_receipt takes an optional module_results field."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llmtool import remote_test as rt  # noqa: E402

SHA = 'b' * 40
MODULES = ('test_a', 'test_b')
LOG_PATH = '/tmp/llmtool-remote-log.Abc123/remote-test.log'


def raw(result='PASS', **changes):
    body = dict(result=result, exit_code=0 if result == 'PASS' else 1, source_sha=SHA)
    body.update(tree='c' * 40, fetch_head=SHA, modules=list(MODULES), identity=dict(uid=65534))
    body.update(started_at='2026-11-16T00:00:00Z', ended_at='2026-11-16T00:00:01Z', elapsed_s=1.0)
    body.update(ran_total=5, final=['OK', 'OK'], failures=[], log_bytes=0, log_sha256='d' * 64)
    body.update(remote_log_path=LOG_PATH)
    body.update(changes)
    return json.dumps(body, separators=(',', ':')).encode()


def results(first='OK', second='OK', ran=(2, 3)):
    one = dict(module='test_a', ran=ran[0], skipped=0, final=first)
    return [one, dict(module='test_b', ran=ran[1], skipped=0, final=second)]


def validate(data):
    return rt._validate_remote_receipt(data, sha=SHA, modules=MODULES)


class ValidatorModuleResultsTests(unittest.TestCase):
    def test_legacy_receipt_is_still_accepted(self):
        parsed = validate(raw())
        self.assertIsNotNone(parsed)
        self.assertNotIn('module_results', parsed)

    def test_valid_module_results_are_kept(self):
        parsed = validate(raw(module_results=results()))
        self.assertEqual(parsed['module_results'], results())

    def test_fail_with_excerpt_and_module_results_is_accepted(self):
        final = ['OK', 'FAILED (failures=1)']
        entries = results(second='FAILED (failures=1)')
        extra = dict(failures=['test_b'], module_results=entries, failure_excerpt='AssertionError')
        parsed = validate(raw('FAIL', final=final, **extra))
        self.assertEqual(parsed['module_results'], entries)

    def test_inconsistent_module_results_are_refused(self):
        self.assertIsNone(validate(raw(module_results=results(ran=(2, 4)))))
        self.assertIsNone(validate(raw(module_results=results()[:1])))
        self.assertIsNone(validate(raw(module_results={})))

    def test_unknown_extra_field_is_still_refused(self):
        self.assertIsNone(validate(raw(module_results=results(), note='x')))
        self.assertIsNone(validate(raw(note='x')))


if __name__ == '__main__':
    unittest.main()
