from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import main_incident  # noqa: E402


SHA = 'a' * 40
LATER_SHA = 'b' * 40


def linux(conclusion='success', *, run_id=1, test=None, name='check (Linux 3.14)', **extra):
    run = {'run_id': run_id, 'status': 'completed', 'conclusion': conclusion,
           'name': name}
    if test is not None:
        run['test'] = test
    run.update(extra)
    return run


def full(conclusion='success', *, run_id=2, test=None, jobs=None, **extra):
    if jobs is None:
        jobs = [{'name': name, 'conclusion': 'success'}
                for name in main_incident.mac_full_due.JOBS]
    run = {'run_id': run_id, 'status': 'completed', 'conclusion': conclusion,
           'jobs': jobs}
    if test is not None:
        run['test'] = test
    run.update(extra)
    return run


def head(sha, pr, *, linux_run=None, full_run=None, **extra):
    record = {'sha': sha, 'pr': pr}
    if linux_run is not None:
        record['linux'] = linux_run
    if full_run is not None:
        record['full'] = full_run
    record.update(extra)
    return record


class EnvironmentTests(unittest.TestCase):
    def test_environment_conclusions_are_env(self):
        for conclusion in ('cancelled', 'timed_out', 'startup_failure'):
            with self.subTest(conclusion=conclusion):
                failing = head(
                    SHA, 1,
                    linux_run=linux(conclusion, run_id=10,
                                    test='tools/llmtool/tests/test_x.py::test_y'))
                result = main_incident.classify(failing)
                self.assertEqual(result['class'], 'ENV')
                self.assertTrue(result['evidence'])

    def test_full_environment_conclusion_is_env(self):
        failing = head(
            SHA, 1,
            full_run=full('timed_out', run_id=20,
                          test='tools/llmtool/tests/test_x.py::test_y'))
        self.assertEqual(main_incident.classify(failing)['class'], 'ENV')

    def test_runner_capacity_cancel_is_env(self):
        failing = head(
            SHA, 1,
            linux_run=linux('failure', run_id=11, test='tools/llmtool/tests/test_x.py::test_y',
                            cancel_reason='runner capacity exhausted'))
        self.assertEqual(main_incident.classify(failing)['class'], 'ENV')

    def test_failed_non_test_step_is_env(self):
        failing = head(
            SHA, 1,
            linux_run=linux('failure', run_id=12, failed_step='Install dependencies',
                            step_kind='setup'))
        self.assertEqual(main_incident.classify(failing)['class'], 'ENV')

    def test_missing_or_unparsable_test_id_is_unknown_not_env(self):
        for test in (None, '', '   ', 123, 'bad\nid'):
            with self.subTest(test=test):
                failing = head(SHA, 1, linux_run=linux('failure', run_id=13, test=test))
                result = main_incident.classify(failing)
                self.assertEqual(result['class'], 'UNKNOWN')


class ClassificationTests(unittest.TestCase):
    def test_single_pr_without_reproduction_is_unknown(self):
        records = [
            head('d' * 40, 4,
                 linux_run=linux('failure', run_id=14,
                                 test='tools/llmtool/tests/test_x.py::test_y')),
            head('c' * 40, 3, linux_run=linux('success', run_id=13),
                 full_run=full('success', run_id=23)),
        ]
        incident = main_incident.find_incident(records)
        self.assertEqual(main_incident.classify(incident['failing'])['class'], 'UNKNOWN')
        self.assertEqual(incident['range'], [{'sha': 'd' * 40, 'pr': 4}])

    def test_linux_and_full_same_test_at_one_sha_is_code(self):
        failing = head(
            SHA, 9,
            linux_run=linux('failure', run_id=101,
                            test='tools/llmtool/tests/test_x.py::test_y'),
            full_run=full('failure', run_id=202,
                          test='tools/llmtool/tests/test_x.py::test_y'))
        result = main_incident.classify(failing)
        self.assertEqual(result['class'], 'CODE')
        evidence = ' '.join(result['evidence'])
        self.assertIn('101', evidence)
        self.assertIn('202', evidence)

    def test_two_full_runs_with_same_test_is_code(self):
        failing = head(
            SHA, 9,
            full_run=full('failure', run_id=1,
                          test='tools/llmtool/tests/test_x.py::test_y'))
        result = main_incident.classify(
            failing,
            full_runs=[
                full('failure', run_id=1,
                     test='tools/llmtool/tests/test_x.py::test_y'),
                full('failure', run_id=2,
                     test='tools/llmtool/tests/test_x.py::test_y'),
            ],
        )
        self.assertEqual(result['class'], 'CODE')
        evidence = ' '.join(result['evidence'])
        self.assertIn('1', evidence)
        self.assertIn('2', evidence)

    def test_later_pass_with_no_change_is_flaky(self):
        failing = head(
            SHA, 2,
            linux_run=linux('failure', run_id=7,
                            test='tools/llmtool/tests/test_x.py::test_y'))
        result = main_incident.classify(
            failing,
            later_passes=[{
                'sha': LATER_SHA,
                'run_id': 8,
                'test': 'tools/llmtool/tests/test_x.py::test_y',
                'conclusion': 'success',
            }],
        )
        self.assertEqual(result['class'], 'FLAKY')

    def test_later_pass_after_a_change_is_not_flaky(self):
        failing = head(
            SHA, 2,
            linux_run=linux('failure', run_id=7,
                            test='tools/llmtool/tests/test_x.py::test_y'))
        result = main_incident.classify(
            failing,
            later_passes=[{
                'sha': LATER_SHA,
                'run_id': 8,
                'test': 'tools/llmtool/tests/test_x.py::test_y',
                'conclusion': 'success',
                'changed': True,
            }],
        )
        self.assertEqual(result['class'], 'UNKNOWN')


class RangeTests(unittest.TestCase):
    def test_last_good_head_and_range_selection(self):
        records = [
            head('d' * 40, 4,
                 linux_run=linux('failure', run_id=14,
                                 test='tools/llmtool/tests/test_x.py::test_y')),
            head('c' * 40, 3),  # no checks, so not a trusted pass
            head('b' * 40, 2, linux_run=linux('success', run_id=12),
                 full_run=full('success', run_id=22)),
            head('a' * 40, 1, linux_run=linux('success', run_id=11),
                 full_run=full('success', run_id=21)),
        ]
        self.assertEqual(
            main_incident.find_last_trusted_head(records)['sha'], 'b' * 40)
        incident = main_incident.find_incident(records)
        self.assertEqual(incident['failing']['sha'], 'd' * 40)
        self.assertEqual(incident['last_good']['sha'], 'b' * 40)
        self.assertEqual(incident['range'], [
            {'sha': 'c' * 40, 'pr': 3},
            {'sha': 'd' * 40, 'pr': 4},
        ])

    def test_untrusted_full_run_is_not_a_last_good_head(self):
        partial_jobs = [{'name': name, 'conclusion': 'success'}
                        for name in main_incident.mac_full_due.JOBS[:-1]]
        records = [
            head('c' * 40, 3, linux_run=linux('failure', run_id=13,
                                              test='tools/llmtool/tests/test_x.py::test_y')),
            head('b' * 40, 2, linux_run=linux('success', run_id=12),
                 full_run=full('success', run_id=22, jobs=partial_jobs)),
        ]
        incident = main_incident.find_incident(records)
        self.assertIsNone(incident['last_good'])
        self.assertEqual(incident['range'], [])


if __name__ == '__main__':
    unittest.main()