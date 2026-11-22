"""#2573: Darwin-only test modules are left to the exact-head Mac CI gate.

Linux remote tests (EC2 and hosted) skip every test in a Darwin-only module,
so the remote script counts it as FAIL although nothing failed. These tests
drive the real module selection, receipt coverage and advance status code.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import remote_test, task_advance

try:
    from tests import test_remote_test as remote_fixtures
    from tests.test_task_advance import base_record, d6, remote_receipt
except ImportError:
    import test_remote_test as remote_fixtures
    from test_task_advance import base_record, d6, remote_receipt


NATIVE = 'test_review_authority_native'
DARWIN_SOURCE = (
    "import sys\nimport unittest\n\n"
    "@unittest.skipUnless(sys.platform == 'darwin', 'Darwin only')\n"
    "class MacTests(unittest.TestCase):\n"
    "    def test_one(self):\n        pass\n")


class DarwinOnlyClassificationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.tests = self.root / 'tests'
        self.tests.mkdir()
        self.manifest = self.root / 'mac-modules.txt'

    def classify(self, sources, listed):
        for stem, source in sources.items():
            (self.tests / f'{stem}.py').write_text(source)
        self.manifest.write_text(''.join(f'{item} # note\n' for item in listed))
        return remote_test._darwin_only_modules(
            sorted(sources), manifest=self.manifest, tests_dir=self.tests)

    def test_listed_and_darwin_skipped_module_is_darwin_only(self):
        self.assertEqual(self.classify({'test_mac': DARWIN_SOURCE}, ['test_mac.MacTests']),
                         {'test_mac'})

    def test_doubt_keeps_the_module_remote(self):
        cases = {
            'unlisted': ({'test_mac': DARWIN_SOURCE}, []),
            'no_skip': ({'test_mac': DARWIN_SOURCE.replace(
                "@unittest.skipUnless(sys.platform == 'darwin', 'Darwin only')\n", '')},
                ['test_mac.MacTests']),
            'linux_skip': ({'test_mac': DARWIN_SOURCE.replace("'darwin'", "'linux'")},
                           ['test_mac.MacTests']),
            'extra_case': ({'test_mac': DARWIN_SOURCE +
                            "\nclass LinuxTests(unittest.TestCase):\n"
                            "    def test_two(self):\n        pass\n"},
                           ['test_mac.MacTests']),
            'module_function': ({'test_mac': DARWIN_SOURCE + "\ndef test_free():\n    pass\n"},
                                ['test_mac.MacTests']),
            'syntax_error': ({'test_mac': 'class (:\n'}, ['test_mac.MacTests']),
            'no_cases': ({'test_mac': 'import sys\n'}, ['test_mac.MacTests']),
        }
        for name, (sources, listed) in cases.items():
            with self.subTest(name):
                self.assertEqual(self.classify(sources, listed), frozenset())

    def test_missing_manifest_or_module_keeps_everything_remote(self):
        (self.tests / 'test_mac.py').write_text(DARWIN_SOURCE)
        self.assertEqual(remote_test._darwin_only_modules(
            ['test_mac'], manifest=self.root / 'absent.txt', tests_dir=self.tests), frozenset())
        self.manifest.write_text('test_absent.MacTests\n')
        self.assertEqual(remote_test._darwin_only_modules(
            ['test_absent'], manifest=self.manifest, tests_dir=self.tests), frozenset())

    def test_real_checkout_classifies_the_2573_module_only(self):
        found = remote_test._darwin_only_modules(
            [NATIVE, 'test_remote_test', 'test_chat_tui_pty',
             'test_developer_write_boundary', 'test_task_review'])
        self.assertEqual(found, {NATIVE})


class RemoteRunSelectionTests(unittest.TestCase):
    # Reuse the real fixture methods without re-running RemoteTestTests here.
    receipt = remote_fixtures.RemoteTestTests.receipt
    run_it = remote_fixtures.RemoteTestTests.run_it

    def setUp(self):
        remote_fixtures.RemoteTestTests.setUp(self)
        self.store.task['developer']['read_paths'] = [
            f'tools/llmtool/tests/{NATIVE}.py', 'tools/llmtool/tests/test_remote_test.py']

    def test_required_modules_leave_darwin_only_to_mac_ci(self):
        self.assertEqual(remote_test._task_path_modules(self.store.task),
                         ('test_remote_test', NATIVE))
        self.assertEqual(remote_test.remote_required_modules(self.store.task),
                         ('test_remote_test',))

    def test_default_run_skips_darwin_only_module_and_passes(self):
        payload = json.loads(self.receipt())
        payload['module_results'] = [dict(module='test_remote_test', ran=1, skipped=0,
                                          final='OK')]
        result = self.run_it(json.dumps(payload).encode(), modules=())
        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(self.calls[-1][0][-1], 'test_remote_test')
        self.assertNotIn(NATIVE, self.calls[-1][0])

    def test_explicit_darwin_only_module_is_not_admitted(self):
        result = self.run_it(modules=(NATIVE,))
        self.assertEqual(result['code'], 'MODULE_NOT_ADMITTED')
        self.assertEqual(self.calls, [])


class AdvanceCoverageTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('llmtool.source_freshness.code_tree', return_value='d' * 64)
        patcher.start()
        self.addCleanup(patcher.stop)

    def record(self, *modules):
        record = base_record()
        record['developer'] = dict(record['developer'], read_paths=[
            f'tools/llmtool/tests/{name}.py' for name in modules], write_paths=[])
        return record

    def test_pass_without_darwin_only_module_covers_the_task(self):
        record = self.record(NATIVE, 'test_task_advance')
        record['remote_test_receipts'] = [remote_receipt(
            record, module_results=[dict(module='test_task_advance', ran=1, skipped=0,
                                         final='OK')])]
        status, _excerpt, receipt = task_advance._remote_test_status(record, d6())
        self.assertEqual(status, 'PASS')
        self.assertEqual(receipt['modules'], ['test_task_advance'])

    def test_ordinary_module_that_ran_nothing_still_fails_closed(self):
        record = self.record('test_task_advance')
        receipt = remote_receipt(
            record, final=['OK (skipped=1)'], ran_total=1,
            module_results=[dict(module='test_task_advance', ran=1, skipped=1,
                                 final='OK (skipped=1)')])
        self.assertFalse(remote_test.pass_receipt_covers(
            receipt, remote_test.remote_required_modules(record)))
        record['remote_test_receipts'] = [receipt]
        status, _excerpt, _receipt = task_advance._remote_test_status(record, d6())
        self.assertNotEqual(status, 'PASS')

    def test_missing_ordinary_module_is_not_covered(self):
        record = self.record(NATIVE, 'test_task_advance', 'test_remote_test')
        record['remote_test_receipts'] = [remote_receipt(
            record, module_results=[dict(module='test_task_advance', ran=1, skipped=0,
                                         final='OK')])]
        status, _excerpt, _receipt = task_advance._remote_test_status(record, d6())
        self.assertNotEqual(status, 'PASS')


if __name__ == '__main__':
    unittest.main()
