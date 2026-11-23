"""#2602 item u-sec: aggregate bounds on precise referencing-tests search."""

import re
import subprocess
import types
import unittest
from unittest import mock

from llmtool import read_scope_advice as advice
from llmtool import task_store, task_submission_cli

WIDGET = 'tools/llmtool/llmtool/widget.py'
TEST_A = 'tools/llmtool/tests/test_widget_a.py'
TEST_B = 'tools/llmtool/tests/test_widget_b.py'


def _module_source(count):
    return '\n\n'.join(f'def fn_{i:02d}():\n    return {i}' for i in range(count)) + '\n'


def _is_module_grep(argv):
    return argv[3] == 'grep' and argv.count('-e') > 1


class ReadScopeCapTest(unittest.TestCase):
    def test_bound_constants_exist(self):
        self.assertGreater(advice.MAX_PRECISE_IDENTIFIERS, 0)
        self.assertGreater(advice.MAX_PRECISE_GREPS, 0)
        self.assertGreater(advice.PRECISE_SEARCH_MAX_SECONDS, 0)
        self.assertGreater(advice.PRECISE_CALL_TIMEOUT_SECONDS, 0)
        self.assertEqual(advice.TEST_REFERENCES_CAPPED, 'TEST_REFERENCES_CAPPED')

    def test_identifier_bound_caps_subprocesses_and_falls_back_with_note(self):
        total = advice.MAX_PRECISE_IDENTIFIERS + 3
        source = _module_source(total)
        precise_greps = []

        def runner(argv, **kwargs):
            if argv[3] == 'show':
                return types.SimpleNamespace(returncode=0, stdout=source)
            if _is_module_grep(argv):
                return types.SimpleNamespace(returncode=0, stdout=f'HEAD:{TEST_A}\n')
            precise_greps.append(argv)
            return types.SimpleNamespace(returncode=0, stdout=f'HEAD:{TEST_B}\n')

        texts = ['Update ' + ', '.join(f'fn_{i:02d}()' for i in range(total))]
        items = advice.referencing_tests(
            root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
            runner=runner, texts=texts)
        fallback = advice.referencing_tests(
            root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
            runner=runner)

        self.assertLessEqual(len(precise_greps), advice.MAX_PRECISE_IDENTIFIERS)
        self.assertEqual(items[:-1], fallback)
        self.assertEqual(items[-1]['kind'], advice.TEST_REFERENCES_CAPPED)
        self.assertIsNone(items[-1]['path'])
        self.assertIn('capped', items[-1]['note'])
        self.assertIn('identifier', items[-1]['note'])
        task_store._validate_read_scope_advice([advice.entry(items, None, None)])

    def test_grep_bound_stops_further_greps_and_falls_back_with_note(self):
        source = _module_source(4)
        precise_greps = []

        def runner(argv, **kwargs):
            if argv[3] == 'show':
                return types.SimpleNamespace(returncode=0, stdout=source)
            if _is_module_grep(argv):
                return types.SimpleNamespace(returncode=0, stdout=f'HEAD:{TEST_A}\n')
            precise_greps.append(argv)
            return types.SimpleNamespace(returncode=0, stdout=f'HEAD:{TEST_B}\n')

        texts = ['Update fn_00(), fn_01(), fn_02(), fn_03()']
        with mock.patch.object(advice, 'MAX_PRECISE_GREPS', 2):
            items = advice.referencing_tests(
                root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
                runner=runner, texts=texts)
        fallback = advice.referencing_tests(
            root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
            runner=runner)

        self.assertEqual(len(precise_greps), 2)
        self.assertEqual(items[:-1], fallback)
        self.assertEqual(items[-1]['kind'], advice.TEST_REFERENCES_CAPPED)
        self.assertIsNone(items[-1]['path'])
        self.assertIn('capped', items[-1]['note'])
        self.assertIn('git grep', items[-1]['note'])
        task_store._validate_read_scope_advice([advice.entry(items, None, None)])

    def test_time_budget_stops_early_and_clamps_per_call_timeout(self):
        source = _module_source(5)
        now = [0.0]
        step = advice.PRECISE_SEARCH_MAX_SECONDS / 2.5
        precise_calls = []
        recorded_timeouts = []

        def fake_monotonic():
            return now[0]

        def runner(argv, **kwargs):
            if _is_module_grep(argv):
                return types.SimpleNamespace(returncode=0, stdout=f'HEAD:{TEST_A}\n')
            remaining_before = advice.PRECISE_SEARCH_MAX_SECONDS - now[0]
            recorded_timeouts.append((kwargs['timeout'], remaining_before))
            now[0] += step
            if argv[3] == 'show':
                return types.SimpleNamespace(returncode=0, stdout=source)
            precise_calls.append(argv)
            return types.SimpleNamespace(returncode=0, stdout=f'HEAD:{TEST_B}\n')

        texts = ['Update fn_00(), fn_01(), fn_02(), fn_03(), fn_04()']
        with mock.patch.object(advice.time, 'monotonic', side_effect=fake_monotonic):
            items = advice.referencing_tests(
                root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
                runner=runner, texts=texts)
        fallback = advice.referencing_tests(
            root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
            runner=runner)

        self.assertLess(len(precise_calls), 5)
        self.assertTrue(recorded_timeouts)
        for timeout_arg, remaining_before in recorded_timeouts:
            self.assertGreater(timeout_arg, 0)
            self.assertLessEqual(timeout_arg, remaining_before)
        self.assertEqual(items[:-1], fallback)
        self.assertEqual(items[-1]['kind'], advice.TEST_REFERENCES_CAPPED)
        self.assertIsNone(items[-1]['path'])
        self.assertIn('capped', items[-1]['note'])
        self.assertIn('time', items[-1]['note'])
        task_store._validate_read_scope_advice([advice.entry(items, None, None)])

    def test_timeout_expired_at_deadline_falls_back_with_time_cap_note(self):
        source = _module_source(2)
        now = [0.0]

        def runner(argv, **kwargs):
            if _is_module_grep(argv):
                return types.SimpleNamespace(returncode=0, stdout=f'HEAD:{TEST_A}\n')
            if argv[3] == 'show':
                return types.SimpleNamespace(returncode=0, stdout=source)
            now[0] += advice.PRECISE_SEARCH_MAX_SECONDS + 1.0
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])

        with mock.patch.object(advice.time, 'monotonic', side_effect=lambda: now[0]):
            items = advice.referencing_tests(
                root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
                runner=runner, texts=['Update fn_00(), fn_01()'])
        fallback = advice.referencing_tests(
            root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
            runner=runner)

        self.assertEqual(items[:-1], fallback)
        self.assertEqual(items[-1]['kind'], advice.TEST_REFERENCES_CAPPED)
        self.assertIn('time', items[-1]['note'])

    def test_final_grep_returning_after_deadline_falls_back_with_time_cap_note(self):
        source = _module_source(1)
        now = [0.0]

        def runner(argv, **kwargs):
            if _is_module_grep(argv):
                return types.SimpleNamespace(returncode=0, stdout=f'HEAD:{TEST_A}\n')
            if argv[3] == 'show':
                return types.SimpleNamespace(returncode=0, stdout=source)
            now[0] += advice.PRECISE_SEARCH_MAX_SECONDS + 1.0
            return types.SimpleNamespace(returncode=0, stdout=f'HEAD:{TEST_B}\n')

        with mock.patch.object(advice.time, 'monotonic', side_effect=lambda: now[0]):
            items = advice.referencing_tests(
                root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
                runner=runner, texts=['Update fn_00()'])
        fallback = advice.referencing_tests(
            root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
            runner=runner)

        self.assertEqual(items[:-1], fallback)
        self.assertEqual(items[-1]['kind'], advice.TEST_REFERENCES_CAPPED)
        self.assertIsNone(items[-1]['path'])
        self.assertIn('capped', items[-1]['note'])
        self.assertIn('time', items[-1]['note'])
        task_store._validate_read_scope_advice([advice.entry(items, None, None)])

    def test_module_show_returning_after_deadline_falls_back_with_time_cap_note(self):
        source = _module_source(1)
        now = [0.0]

        def runner(argv, **kwargs):
            if _is_module_grep(argv):
                return types.SimpleNamespace(returncode=0, stdout=f'HEAD:{TEST_A}\n')
            if argv[3] == 'show':
                now[0] += advice.PRECISE_SEARCH_MAX_SECONDS + 1.0
                return types.SimpleNamespace(returncode=0, stdout=source)
            raise AssertionError('git grep should not start after deadline')

        with mock.patch.object(advice.time, 'monotonic', side_effect=lambda: now[0]):
            items = advice.referencing_tests(
                root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
                runner=runner, texts=['Update fn_00()'])
        fallback = advice.referencing_tests(
            root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
            runner=runner)

        self.assertEqual(items[:-1], fallback)
        self.assertEqual(items[-1]['kind'], advice.TEST_REFERENCES_CAPPED)
        self.assertIsNone(items[-1]['path'])
        self.assertIn('capped', items[-1]['note'])
        self.assertIn('time', items[-1]['note'])
        task_store._validate_read_scope_advice([advice.entry(items, None, None)])

    def test_within_bounds_matches_precise_output_and_historical_no_texts(self):
        source = _module_source(3)

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

        items = advice.referencing_tests(
            root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
            runner=runner, texts=['Change fn_00() and fn_01()'])

        self.assertEqual([(i['kind'], i['path']) for i in items],
                         [(advice.TEST_LOCKS_CHANGE, TEST_A),
                          (advice.TEST_LOCKS_CHANGE, TEST_B)])
        self.assertIn('fn_00', items[0]['note'])
        self.assertIn('fn_01', items[1]['note'])
        task_store._validate_read_scope_advice([advice.entry(items, None, None)])

        historical = advice.referencing_tests(
            root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
            runner=runner)
        self.assertEqual([(i['kind'], i['path']) for i in historical],
                         [(advice.TEST_REFERENCES_WRITE, TEST_A),
                          (advice.TEST_REFERENCES_WRITE, TEST_B)])

    def test_cap_note_survives_caller_max_items_when_fallback_fills_limit(self):
        total = advice.MAX_PRECISE_IDENTIFIERS + 1
        source = _module_source(total)
        modules = [f'tools/llmtool/llmtool/mod_{i}.py' for i in range(8)]

        def runner(argv, **kwargs):
            if argv[3] == 'show':
                spec = argv[4]
                return types.SimpleNamespace(
                    returncode=0, stdout=source if spec.endswith('mod_0.py') else '')
            if _is_module_grep(argv):
                match = re.search(r'mod_(\d+)\\\.py', argv[7])
                idx = int(match.group(1))
                out = ''.join(
                    f'HEAD:tools/llmtool/tests/test_mod_{idx}_{j}.py\n'
                    for j in range(advice.MAX_TESTS_PER_PATH)
                )
                return types.SimpleNamespace(returncode=0, stdout=out)
            raise AssertionError(f'unexpected git call: {argv}')

        fallback = advice.referencing_tests(
            root='/fake', commit='HEAD', read_paths=[], write_paths=modules,
            runner=runner)
        self.assertEqual(len(fallback), advice.MAX_ITEMS)

        goal = 'Update ' + ', '.join(f'fn_{i:02d}()' for i in range(total))
        args = types.SimpleNamespace(
            task_id='1' * 32, provider='agy', acceptance=['none'],
            repair_packet=None, high_debug_task=None,
            read_path=[], write_path=modules,
            read_scope_reason=None, read_scope_check=False, cwd='/fake')
        store = mock.Mock()
        store.get.return_value = {'pm_assignment': {'goal': goal}}

        with (mock.patch.object(advice.subprocess, 'run', side_effect=runner),
              mock.patch.object(advice, 'missing_read_paths', return_value=[]),
              mock.patch.object(advice, 'companion_items', return_value=[]),
              mock.patch.object(advice, 'route_path_items', return_value=[]),
              mock.patch.object(advice, 'shell_items', return_value=[]),
              mock.patch.object(advice, 'size_items', return_value=[])):
            result = task_submission_cli._scope_advice_items(args, store)

        self.assertLessEqual(len(result), advice.MAX_ITEMS)
        self.assertEqual(len(result), advice.MAX_ITEMS)
        capped = [item for item in result if item['kind'] == advice.TEST_REFERENCES_CAPPED]
        self.assertEqual(len(capped), 1)
        self.assertEqual(result[:-1], fallback[:advice.MAX_ITEMS - 1])
        self.assertEqual(result[-1]['kind'], advice.TEST_REFERENCES_CAPPED)
        self.assertIsNone(result[-1]['path'])
        task_store._validate_read_scope_advice([advice.entry(result, None, None)])


if __name__ == '__main__':
    unittest.main()
