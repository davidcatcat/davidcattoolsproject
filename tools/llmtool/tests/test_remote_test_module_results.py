"""#2119 WP-1 part (a): remote_test.valid_module_results per design section 3, not wired."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llmtool import remote_test as rt  # noqa: E402

MODULES = ('test_a', 'test_b')


def entry(module, ran, final, skipped=0):
    return dict(module=module, ran=ran, skipped=skipped, final=final)


def receipt(result, entries, modules=MODULES):
    finals = [item['final'] for item in entries]
    total = sum(item['ran'] for item in entries)
    made = dict(result=result, modules=list(modules), final=finals, ran_total=total)
    made['module_results'] = entries
    return made


def passing():
    return [entry('test_a', 0, 'OK (skipped=1)', 1), entry('test_b', 12, 'OK')]


class ValidModuleResultsTests(unittest.TestCase):
    def test_skip_only_module_beside_a_normal_module_is_valid(self):
        self.assertTrue(rt.valid_module_results(receipt('PASS', passing())))

    def test_ran_five_skipped_two_is_valid(self):
        entries = [entry('test_a', 5, 'OK (skipped=2)', 2), entry('test_b', 1, 'OK')]
        self.assertTrue(rt.valid_module_results(receipt('PASS', entries)))

    def test_legacy_receipt_without_the_field_is_accepted(self):
        legacy = receipt('PASS', passing())
        del legacy['module_results']
        self.assertTrue(rt.valid_module_results(legacy))

    def test_fail_may_cover_a_prefix_of_modules(self):
        entries = [entry('test_a', 3, 'FAILED (failures=1)')]
        self.assertTrue(rt.valid_module_results(receipt('FAIL', entries)))

    def test_pass_needs_every_module(self):
        self.assertFalse(rt.valid_module_results(receipt('PASS', passing()[:1])))

    def test_pass_needs_passing_finals(self):
        entries = [entry('test_a', 3, 'FAILED (failures=1)'), entry('test_b', 1, 'OK')]
        self.assertFalse(rt.valid_module_results(receipt('PASS', entries)))

    def test_inconsistent_receipts_are_refused(self):
        base = receipt('PASS', passing())
        cases = dict(sum=dict(ran_total=13), order=dict(modules=['test_b', 'test_a']))
        cases.update(not_a_list=dict(module_results={}))
        cases.update(fewer_finals=dict(final=['OK (skipped=1)']))
        cases.update(other_final=dict(final=['OK (skipped=1)', 'OK (skipped=3)']))
        for name, change in cases.items():
            with self.subTest(case=name):
                bad = dict(copy.deepcopy(base), **change)
                self.assertFalse(rt.valid_module_results(bad))

    def test_bad_entries_are_refused(self):
        changes = dict(skipped_disagrees=dict(skipped=0), extra_key=dict(note='x'))
        changes.update(negative_skipped=dict(skipped=-1), text_count=dict(skipped='1'))
        for name, change in changes.items():
            with self.subTest(case=name):
                bad = receipt('PASS', passing())
                bad['module_results'][0].update(change)
                self.assertFalse(rt.valid_module_results(bad))

    def test_negative_and_bool_ran_are_refused(self):
        for ran in (-1, True):
            with self.subTest(ran=ran):
                bad = receipt('FAIL', [entry('test_a', ran, 'FAILED (errors=1)')])
                self.assertFalse(rt.valid_module_results(bad))


if __name__ == '__main__':
    unittest.main()
