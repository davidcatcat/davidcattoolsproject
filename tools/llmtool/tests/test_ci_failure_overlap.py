"""#2124: ci_failure_overlap.overlap decides whether failing modules relate to the PR."""
import unittest

from llmtool import ci_failure_overlap as ov

STORE = 'tools/llmtool/llmtool/task_store.py'
STORE_TEST = 'tools/llmtool/tests/test_task_store.py'


class CiFailureOverlapTests(unittest.TestCase):
    def test_unrelated_failure_is_no_overlap(self):
        # The #2120 case: monitor tests failed on a PR that touched other files.
        changed = [STORE, STORE_TEST]
        self.assertEqual(ov.overlap(['test_llmtool_monitor'], changed), ov.NO_OVERLAP)

    def test_changed_test_file_or_source_stem_overlaps(self):
        case_a = (['test_task_store'], [STORE_TEST])
        case_b = ([STORE_TEST], [STORE])
        case_c = (['test_llmtool_monitor.py'], ['scripts/llmtool_monitor.py'])
        for failing, changed in (case_a, case_b, case_c):
            with self.subTest(failing=failing):
                self.assertEqual(ov.overlap(failing, changed), ov.OVERLAP)

    def test_declared_module_overlaps(self):
        declared = ['test_remote_test']
        result = ov.overlap(['test_remote_test'], ['docs/x.md'], declared_modules=declared)
        self.assertEqual(result, ov.OVERLAP)

    def test_any_one_related_failure_is_enough(self):
        failing = ['test_llmtool_monitor', 'test_task_store']
        self.assertEqual(ov.overlap(failing, [STORE]), ov.OVERLAP)

    def test_malformed_or_empty_input_is_unknown(self):
        cases = [([], ['a.py']), (None, ['a.py']), (['not a module'], ['a.py'])]
        cases += [(['test_x'], 'a.py'), (['test_x'], [None]), ([7], ['a.py'])]
        cases += [(['test_x'], ['a.py'], None)]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(ov.overlap(*args), ov.UNKNOWN)

    def test_malformed_declared_module_is_unknown(self):
        cases = [['not a module'], [''], ['test_x/'], [None], [7]]
        for declared in cases:
            with self.subTest(declared=declared):
                result = ov.overlap(
                    ['test_x'], ['a.py'], declared_modules=declared)
                self.assertEqual(result, ov.UNKNOWN)

    def test_blank_or_directory_changed_path_is_unknown(self):
        cases = [[''], ['   '], ['tools/llmtool/tests/'], [STORE, '']]
        for changed in cases:
            with self.subTest(changed=changed):
                self.assertEqual(ov.overlap(['test_x'], changed), ov.UNKNOWN)


if __name__ == '__main__':
    unittest.main()
