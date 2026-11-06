"""Focused tests for the offline, caller-asserted test-run summary."""

import hashlib
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

import llmtool.test_run_summary as summary_module
from llmtool.test_run_summary import summarize_test_run
from llmtool.test_selection import select_test_plan


HEAD = 'e1b4ea2c85ff320d694168112da8fa531f126228'
PLAN = select_test_plan(['tools/llmtool/llmtool/task_lifecycle.py'])


def ref(value):
    return 'sha256:' + hashlib.sha256(value).hexdigest()


def receipt(plan=PLAN):
    plan_digest = ref(json.dumps(plan, ensure_ascii=True, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False).encode('ascii'))
    commands = []
    for level in plan['levels']:
        commands_for_level = ([level['command']] if level['level'] == 'L0'
                              else [item['argv'] for item in level.get('commands', [])])
        for argv in commands_for_level:
            log_digest = ref(b'synthetic caller-asserted log digest input')
            counts = ({'tests_run': 0, 'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 0}
                      if argv == ['make', 'syntax'] else
                      {'tests_run': 1, 'passed': 1, 'failed': 0, 'errors': 0, 'skipped': 0})
            commands.append({
                'argv_sha256': ref(json.dumps(argv, ensure_ascii=True, sort_keys=True,
                                              separators=(',', ':'), allow_nan=False).encode('ascii')),
                'exit_status': 0,
                'log_sha256': log_digest,
                'log_ref': log_digest,
                'counts': counts,
                'failure_ids': [],
            })
    return {'schema_version': 1, 'head_sha': HEAD, 'plan_sha256': plan_digest,
            'commands': commands}


class TestRunSummaryTests(unittest.TestCase):
    def test_summary_is_deterministic_bounded_and_explicitly_unverified(self):
        source = receipt()
        first = summarize_test_run(PLAN, source, expected_head=HEAD)
        second = summarize_test_run(PLAN, deepcopy(source), expected_head=HEAD)

        self.assertEqual(first, second)
        self.assertEqual(first['reported_status'], 'ALL_EXIT_ZERO')
        self.assertEqual(first['head_sha'], HEAD)
        self.assertEqual(first['selected_tests'], [
            'tools/llmtool/tests/test_task_lifecycle.py',
            'tools/llmtool/tests/test_task_lifecycle_cli.py',
        ])
        self.assertEqual(first['provenance'], 'CALLER_ASSERTED_UNVERIFIED')
        self.assertFalse(first['execution_verified'])
        self.assertFalse(first['logs_verified'])
        self.assertEqual(first['commands'][0]['failure_ids_completeness'], 'NOT_VERIFIED')
        self.assertEqual(first['commands'][0]['log_ref'], first['commands'][0]['log_sha256'])
        self.assertLessEqual(len(json.dumps(first, sort_keys=True).encode()), 64 * 1024)

    def test_nonzero_command_exit_is_reported_as_failed_not_verified(self):
        source = receipt()
        source['commands'][0]['exit_status'] = 2
        summary = summarize_test_run(PLAN, source, expected_head=HEAD)

        self.assertEqual(summary['reported_status'], 'HAS_NONZERO_EXIT')
        self.assertFalse(summary['execution_verified'])

    def test_receipt_must_bind_to_expected_head_and_plan_digest(self):
        source = receipt()
        with self.assertRaises(ValueError):
            summarize_test_run(PLAN, source, expected_head='0' * 40)
        source['plan_sha256'] = ref(b'other plan')
        with self.assertRaises(ValueError):
            summarize_test_run(PLAN, source, expected_head=HEAD)

    def test_every_command_digest_and_log_reference_is_bound(self):
        source = receipt()
        source['commands'][0]['argv_sha256'] = ref(b'other argv')
        with self.assertRaises(ValueError):
            summarize_test_run(PLAN, source, expected_head=HEAD)

        source = receipt()
        source['commands'][0]['log_ref'] = '/private/log.txt'
        with self.assertRaises(ValueError):
            summarize_test_run(PLAN, source, expected_head=HEAD)

    def test_unmapped_high_risk_and_stale_plans_fail_closed(self):
        for plan in (
            select_test_plan(['tools/llmtool/llmtool/not_mapped.py']),
            select_test_plan(['tools/llmtool/llmtool/test_output_excerpts.py']),
        ):
            with self.subTest(paths=plan['changed_paths']):
                with self.assertRaises(ValueError):
                    summarize_test_run(plan, receipt(), expected_head=HEAD)

        stale = deepcopy(PLAN)
        stale['impact_map_version'] += 1
        with self.assertRaises(ValueError):
            summarize_test_run(stale, receipt(), expected_head=HEAD)

    def test_raw_logs_excerpts_paths_and_unknown_fields_are_rejected(self):
        for key, value in (('raw_log', 'secret text'), ('excerpt', 'source text'),
                           ('path', '/private/log.txt'), ('unexpected', True)):
            source = receipt()
            source[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                summarize_test_run(PLAN, source, expected_head=HEAD)

        source = receipt()
        source['commands'][0]['raw_log'] = 'private raw output'
        with self.assertRaises(ValueError):
            summarize_test_run(PLAN, source, expected_head=HEAD)

    def test_counts_ids_exits_and_secret_like_ids_are_fail_closed(self):
        mutations = []

        source = receipt()
        source['commands'][0]['counts']['tests_run'] = True
        mutations.append(source)

        source = receipt()
        source['commands'][0]['counts']['tests_run'] = 2
        mutations.append(source)

        source = receipt()
        source['commands'][0]['exit_status'] = -1
        mutations.append(source)

        source = receipt()
        source['commands'][0]['failure_ids'] = ['api_key=SECRET']
        mutations.append(source)

        source = receipt()
        source['commands'][0]['failure_ids'] = ['AKIA1234567890ABCDEF']
        mutations.append(source)

        for index, source in enumerate(mutations):
            with self.subTest(case=index), self.assertRaises(ValueError):
                summarize_test_run(PLAN, source, expected_head=HEAD)

    def test_only_flat_relative_pytest_node_paths_are_allowed(self):
        source = receipt()
        source['commands'][1]['counts'] = {'tests_run': 1, 'passed': 0, 'failed': 1,
                                           'errors': 0, 'skipped': 0}
        source['commands'][1]['failure_ids'] = ['tests/test_lifecycle.py::test_done[param-1]']
        source['commands'][1]['exit_status'] = 1
        summary = summarize_test_run(PLAN, source, expected_head=HEAD)
        self.assertEqual(summary['commands'][1]['failure_ids'],
                         ['tests/test_lifecycle.py::test_done[param-1]'])

        for identifier in ('/tmp/test_lifecycle.py::test_done',
                           'tests/../test_lifecycle.py::test_done',
                           'private/test_lifecycle.py::test_done'):
            source = receipt()
            source['commands'][1]['counts'] = {'tests_run': 1, 'passed': 0, 'failed': 1,
                                               'errors': 0, 'skipped': 0}
            source['commands'][1]['failure_ids'] = [identifier]
            source['commands'][1]['exit_status'] = 1
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                summarize_test_run(PLAN, source, expected_head=HEAD)

    def test_oversize_deep_and_wide_inputs_are_rejected_before_serialization(self):
        oversized_plan = {'changed_paths': [],
                          'extra': 'x' * (summary_module.MAX_INPUT_STRING_CHARS + 1)}
        valid_receipt = receipt()
        with patch('llmtool.test_run_summary.json.dumps',
                   side_effect=AssertionError('serialized unbounded input')):
            with self.assertRaises(ValueError):
                summarize_test_run(oversized_plan, valid_receipt, expected_head=HEAD)

        valid_plan = summary_module._validated_plan(PLAN)
        oversized_receipts = []
        source = receipt()
        source['extra'] = 'x' * (summary_module.MAX_INPUT_STRING_CHARS + 1)
        oversized_receipts.append(source)
        source = receipt()
        nested = []
        for _ in range(summary_module.MAX_INPUT_DEPTH + 1):
            nested = [nested]
        source['extra'] = nested
        oversized_receipts.append(source)
        source = receipt()
        source['extra'] = ['x'] * (summary_module.MAX_INPUT_CONTAINER_ITEMS + 1)
        oversized_receipts.append(source)

        for source in oversized_receipts:
            with patch('llmtool.test_run_summary._validated_plan', return_value=valid_plan):
                with patch('llmtool.test_run_summary.json.dumps',
                           side_effect=AssertionError('serialized unbounded input')):
                    with self.assertRaises(ValueError):
                        summarize_test_run(PLAN, source, expected_head=HEAD)

    def test_inconsistent_success_counts_and_duplicate_ids_are_rejected(self):
        source = receipt()
        source['commands'][0]['counts']['failed'] = 1
        source['commands'][0]['counts']['tests_run'] = 2
        source['commands'][0]['failure_ids'] = ['test_failure']
        with self.assertRaises(ValueError):
            summarize_test_run(PLAN, source, expected_head=HEAD)

        source = receipt()
        source['commands'][0]['counts'] = {'tests_run': 2, 'passed': 0, 'failed': 2,
                                           'errors': 0, 'skipped': 0}
        source['commands'][0]['failure_ids'] = ['test_failure', 'test_failure']
        with self.assertRaises(ValueError):
            summarize_test_run(PLAN, source, expected_head=HEAD)


if __name__ == '__main__':
    unittest.main()
