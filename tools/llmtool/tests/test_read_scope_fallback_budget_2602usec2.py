"""#2602 item u-sec2: capped fallback shares the aggregate referencing-tests budget."""

import subprocess
import types
import unittest
from unittest import mock

from llmtool import read_scope_advice as advice
from llmtool import task_store

WIDGET = 'tools/llmtool/llmtool/widget.py'
TEST_A = 'tools/llmtool/tests/test_widget_a.py'
TEST_B = 'tools/llmtool/tests/test_widget_b.py'


def _module_source(count):
    return '\n\n'.join(f'def fn_{i:02d}():\n    return {i}' for i in range(count)) + '\n'


def _is_module_grep(argv):
    return argv[3] == 'grep' and argv.count('-e') > 1


class FallbackBudgetTest(unittest.TestCase):
    def test_identifier_cap_with_many_write_paths_bounds_fallback_greps(self):
        total = advice.MAX_PRECISE_IDENTIFIERS + 4
        source = _module_source(total)
        write_paths = [f'tools/llmtool/llmtool/mod_{i:03d}.py' for i in range(250)]
        grep_calls = []
        now = [0.0]

        def runner(argv, **kwargs):
            if argv[3] == 'show':
                return types.SimpleNamespace(
                    returncode=0, stdout=source if argv[4].endswith('mod_000.py') else '')
            grep_calls.append((argv, kwargs['timeout']))
            return types.SimpleNamespace(
                returncode=0,
                stdout=f'HEAD:tools/llmtool/tests/test_ref_{len(grep_calls):03d}.py\n')

        texts = ['Update ' + ', '.join(f'fn_{i:02d}()' for i in range(total))]
        with mock.patch.object(advice.time, 'monotonic', side_effect=lambda: now[0]):
            items = advice.referencing_tests(
                root='/fake', commit='HEAD', read_paths=[], write_paths=write_paths,
                runner=runner, texts=texts)

        self.assertEqual(len(grep_calls), advice.MAX_PRECISE_GREPS)
        self.assertLessEqual(len(items), advice.MAX_ITEMS)
        capped = [item for item in items if item['kind'] == advice.TEST_REFERENCES_CAPPED]
        self.assertEqual(len(capped), 1)
        self.assertEqual(items[0], capped[0])
        self.assertIsNone(items[0]['path'])
        self.assertIn('capped', items[0]['note'])
        self.assertIn('partial', items[0]['note'])
        self.assertEqual(len(items[1:]), advice.MAX_PRECISE_GREPS)
        self.assertTrue(all(item['kind'] == advice.TEST_REFERENCES_WRITE for item in items[1:]))
        task_store._validate_read_scope_advice([advice.entry(items, None, None)])

    def test_fallback_time_exhaustion_stops_early_and_clamps_timeouts(self):
        total = advice.MAX_PRECISE_IDENTIFIERS + 2
        source = _module_source(total)
        write_paths = [f'tools/llmtool/llmtool/mod_{i:02d}.py' for i in range(10)]
        now = [0.0]
        step = advice.PRECISE_SEARCH_MAX_SECONDS / 4.0
        recorded_timeouts = []
        fallback_greps = []

        def runner(argv, **kwargs):
            if argv[3] == 'show':
                return types.SimpleNamespace(
                    returncode=0, stdout=source if argv[4].endswith('mod_00.py') else '')
            remaining_before = advice.PRECISE_SEARCH_MAX_SECONDS - now[0]
            recorded_timeouts.append((kwargs['timeout'], remaining_before))
            fallback_greps.append(argv)
            now[0] += step
            return types.SimpleNamespace(
                returncode=0,
                stdout=f'HEAD:tools/llmtool/tests/test_ref_{len(fallback_greps):02d}.py\n')

        texts = ['Update ' + ', '.join(f'fn_{i:02d}()' for i in range(total))]
        with mock.patch.object(advice.time, 'monotonic', side_effect=lambda: now[0]):
            items = advice.referencing_tests(
                root='/fake', commit='HEAD', read_paths=[], write_paths=write_paths,
                runner=runner, texts=texts)

        self.assertEqual(len(fallback_greps), 4)
        self.assertTrue(recorded_timeouts)
        for timeout_arg, remaining_before in recorded_timeouts:
            self.assertGreater(timeout_arg, 0)
            self.assertLessEqual(timeout_arg, remaining_before)
            self.assertLessEqual(timeout_arg, advice.PRECISE_CALL_TIMEOUT_SECONDS)
        self.assertEqual(items[0]['kind'], advice.TEST_REFERENCES_CAPPED)
        self.assertIn('partial', items[0]['note'])
        self.assertEqual(len(items[1:]), 4)
        task_store._validate_read_scope_advice([advice.entry(items, None, None)])

    def test_fallback_timeout_expired_on_last_file_marks_partial(self):
        total = advice.MAX_PRECISE_IDENTIFIERS + 1
        source = _module_source(total)
        now = [0.0]

        def runner(argv, **kwargs):
            if argv[3] == 'show':
                return types.SimpleNamespace(returncode=0, stdout=source)
            now[0] += advice.PRECISE_SEARCH_MAX_SECONDS + 1.0
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])

        texts = ['Update ' + ', '.join(f'fn_{i:02d}()' for i in range(total))]
        with mock.patch.object(advice.time, 'monotonic', side_effect=lambda: now[0]):
            items = advice.referencing_tests(
                root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
                runner=runner, texts=texts)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['kind'], advice.TEST_REFERENCES_CAPPED)
        self.assertIn('partial', items[0]['note'])
        task_store._validate_read_scope_advice([advice.entry(items, None, None)])

    def test_partial_grep_budget_shared_between_precise_and_fallback(self):
        source = _module_source(5)
        write_paths = [f'tools/llmtool/llmtool/mod_{i:02d}.py' for i in range(6)]
        precise_greps = []
        fallback_greps = []

        def runner(argv, **kwargs):
            if argv[3] == 'show':
                return types.SimpleNamespace(
                    returncode=0, stdout=source if argv[4].endswith('mod_00.py') else '')
            if _is_module_grep(argv):
                fallback_greps.append(argv)
                return types.SimpleNamespace(
                    returncode=0,
                    stdout=f'HEAD:tools/llmtool/tests/test_fb_{len(fallback_greps):02d}.py\n')
            precise_greps.append(argv)
            return types.SimpleNamespace(returncode=0, stdout=f'HEAD:{TEST_B}\n')

        texts = ['Update fn_00(), fn_01(), fn_02(), fn_03(), fn_04()']
        with mock.patch.object(advice, 'MAX_PRECISE_GREPS', 4):
            items = advice.referencing_tests(
                root='/fake', commit='HEAD', read_paths=[], write_paths=write_paths,
                runner=runner, texts=texts)

        self.assertEqual(len(precise_greps), 4)
        self.assertEqual(len(fallback_greps), 0)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['kind'], advice.TEST_REFERENCES_CAPPED)
        self.assertIn('partial', items[0]['note'])

    def test_uncapped_and_no_texts_unchanged(self):
        source = _module_source(3)
        now = [0.0]

        def runner(argv, **kwargs):
            if argv[3] == 'show':
                return types.SimpleNamespace(returncode=0, stdout=source)
            if _is_module_grep(argv):
                return types.SimpleNamespace(
                    returncode=0, stdout=f'HEAD:{TEST_A}\nHEAD:{TEST_B}\n')
            if 'fn_00' in argv[7]:
                return types.SimpleNamespace(returncode=0, stdout=f'HEAD:{TEST_A}\n')
            if 'fn_01' in argv[7]:
                return types.SimpleNamespace(returncode=0, stdout=f'HEAD:{TEST_B}\n')
            return types.SimpleNamespace(returncode=1, stdout='')

        with mock.patch.object(advice.time, 'monotonic', side_effect=lambda: now[0]):
            precise = advice.referencing_tests(
                root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
                runner=runner, texts=['Change fn_00() and fn_01()'])
            historical = advice.referencing_tests(
                root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
                runner=runner)

        self.assertEqual([(i['kind'], i['path']) for i in precise],
                         [(advice.TEST_LOCKS_CHANGE, TEST_A),
                          (advice.TEST_LOCKS_CHANGE, TEST_B)])
        self.assertEqual([(i['kind'], i['path']) for i in historical],
                         [(advice.TEST_REFERENCES_WRITE, TEST_A),
                          (advice.TEST_REFERENCES_WRITE, TEST_B)])


if __name__ == '__main__':
    unittest.main()
