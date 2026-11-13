from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from llmtool import main_incident as incident  # noqa: E402


SHA_A = 'a' * 40
SHA_B = 'b' * 40
SHA_C = 'c' * 40
TEST = 'tools/llmtool/tests/test_example.py::test_case'


def mac_run(run_id, sha, conclusion='failure', **extra):
    run = {'id': run_id, 'status': 'completed', 'conclusion': conclusion,
           'head_sha': sha, 'workflow': 'llmtool-mac-ci'}
    run.update(extra)
    return run


def linux_run(run_id, sha, conclusion='failure', **extra):
    run = {'id': run_id, 'status': 'completed', 'conclusion': conclusion,
           'head_sha': sha, 'workflow': 'llmtool-linux-ci',
           'name': 'check (Linux 3.14)'}
    run.update(extra)
    return run


class MainIncidentTests(unittest.TestCase):
    def test_classify_incident_end_to_end(self):
        runs = [
            mac_run(5, SHA_C, test_id=TEST, pr=17),
            linux_run(4, SHA_C, test_id=TEST, pr=17),
            mac_run(3, SHA_B, 'success'),
            linux_run(2, SHA_B, 'success'),
        ]
        self.assertEqual(incident.trusted_pass(runs), SHA_B)
        self.assertEqual(incident.head_failed(runs), SHA_C)
        self.assertEqual(incident.incident_range(runs, SHA_B, SHA_C),
                         {'commits': [SHA_C], 'prs': [17]})
        verdict, evidence = incident.classify_incident(runs)
        self.assertEqual(verdict, incident.CODE)
        self.assertIn('Linux and FULL Mac', evidence[0])

    def test_newest_first_false(self):
        oldest_first = [
            mac_run(1, SHA_B, 'success'),
            mac_run(2, SHA_A, test_id=TEST),
        ]
        self.assertEqual(incident.trusted_pass(oldest_first, newest_first=False), SHA_B)
        self.assertEqual(incident.head_failed(oldest_first, newest_first=False), SHA_A)
        self.assertEqual(incident.head_failed(list(reversed(oldest_first))), SHA_A)

    def test_no_failing_head(self):
        runs = [mac_run(2, SHA_A, 'success'), linux_run(1, SHA_A, 'success')]
        self.assertIsNone(incident.head_failed(runs))
        verdict, evidence = incident.classify_incident(runs)
        self.assertEqual(verdict, incident.UNKNOWN)
        self.assertTrue(evidence)

    def test_non_dict_inputs(self):
        mixed = [None, 'not a run', 3]
        self.assertIsNone(incident.trusted_pass(mixed))
        self.assertIsNone(incident.head_failed(mixed))
        self.assertIsNone(incident.trusted_pass(None))
        self.assertEqual(incident.classify_incident(mixed),
                         (incident.UNKNOWN, ['no CI run data']))
        self.assertEqual(incident.classify_incident(None)[0], incident.UNKNOWN)
        self.assertEqual(incident.incident_range(mixed, SHA_A, SHA_B),
                         {'commits': [], 'prs': []})
        self.assertEqual(incident._env_evidence(mixed), [])

    def test_changed_true_is_not_flaky(self):
        runs = [
            linux_run(2, SHA_B, 'success', passed_tests=[TEST]),
            mac_run(1, SHA_A, test_id=TEST),
        ]
        self.assertEqual(incident.classify_incident(runs)[0], incident.FLAKY)
        self.assertEqual(incident.classify_incident(runs, changed=True)[0],
                         incident.UNKNOWN)

    def test_later_pass_for_a_different_test_id_is_not_flaky(self):
        runs = [
            linux_run(2, SHA_B, 'success', passed_tests=['other::test']),
            mac_run(1, SHA_A, test_id=TEST),
        ]
        self.assertEqual(incident.classify_incident(runs)[0], incident.UNKNOWN)

    def test_capacity_cancel_is_env_and_not_a_failing_head(self):
        runs = [mac_run(1, SHA_A, 'cancelled', reason='runner capacity')]
        self.assertIsNone(incident.head_failed(runs))
        verdict, evidence = incident.classify_incident(runs)
        self.assertEqual(verdict, incident.ENV)
        self.assertIn('capacity', ' '.join(evidence))

    def test_inputs_are_not_mutated(self):
        runs = [
            mac_run(3, SHA_C, test_id=TEST,
                    jobs=[{'name': name, 'conclusion': 'success'}
                          for name in incident.JOBS]),
            mac_run(2, SHA_B, 'failure', failed_step='pytest'),
            linux_run(1, SHA_A, 'success', pull_requests=[{'number': 8}]),
        ]
        before = copy.deepcopy(runs)
        incident.trusted_pass(runs)
        incident.head_failed(runs)
        incident.incident_range(runs, SHA_A, SHA_C)
        incident.classify_incident(runs, changed=True)
        self.assertEqual(runs, before)

    # Pre-review probes (#1706 R1 repair 1).

    def test_passing_run_step_does_not_make_env(self):
        runs = [
            linux_run(2, SHA_A, 'success', step_name='Checkout',
                      reason='runner capacity'),
            mac_run(1, SHA_A, test_id=TEST),
        ]
        verdict, evidence = incident.classify_incident(runs)
        self.assertEqual(verdict, incident.UNKNOWN)
        self.assertFalse(any('Checkout' in item for item in evidence))
        self.assertEqual(incident._env_evidence(runs), [])

    def test_code_reproduction_beats_unrelated_cancel(self):
        runs = [
            mac_run(4, SHA_B, 'cancelled', reason='superseded'),
            mac_run(3, SHA_A, test_id=TEST),
            linux_run(2, SHA_A, test_id=TEST),
        ]
        self.assertEqual(incident.classify_incident(runs)[0], incident.CODE)

    def test_head_failed_uses_newest_run(self):
        runs = [
            mac_run(3, SHA_B, test_id='newer'),
            mac_run(1, SHA_A, test_id='older'),
        ]
        self.assertEqual(incident.head_failed(runs), SHA_B)
        self.assertEqual(
            incident.head_failed(list(reversed(runs)), newest_first=False), SHA_B)

    # Remaining required evidence cases.

    def test_env_positive_evidence_from_failed_non_test_step(self):
        runs = [mac_run(1, SHA_A, failed_step='Set up job')]
        verdict, evidence = incident.classify_incident(runs)
        self.assertEqual(verdict, incident.ENV)
        self.assertIn('non-test step', evidence[0])

    def test_missing_or_unparsable_test_id_stays_unknown(self):
        missing = [mac_run(1, SHA_A, failed_step='pytest')]
        unparsable = [mac_run(1, SHA_A, test_id=123)]
        for runs in (missing, unparsable):
            verdict, evidence = incident.classify_incident(runs)
            self.assertEqual(verdict, incident.UNKNOWN)
            self.assertIn('no usable test ID', evidence[0])

    def test_flaky_needs_a_newer_different_sha_pass(self):
        older_failure_newer_pass = [
            linux_run(2, SHA_B, 'success'),
            mac_run(1, SHA_A, test_id=TEST),
        ]
        same_sha_rerun = [
            linux_run(2, SHA_A, 'success', passed_tests=[TEST]),
            mac_run(1, SHA_A, test_id=TEST),
        ]
        self.assertEqual(incident.classify_incident(older_failure_newer_pass)[0],
                         incident.FLAKY)
        self.assertEqual(incident.classify_incident(same_sha_rerun)[0],
                         incident.UNKNOWN)

    def test_two_full_runs_are_independent_code_evidence(self):
        runs = [
            mac_run(2, SHA_B, test_id=TEST),
            mac_run(1, SHA_A, test_id=TEST),
        ]
        self.assertEqual(incident.classify_incident(runs)[0], incident.CODE)

    def test_one_pr_in_range_is_not_code(self):
        runs = [
            mac_run(2, SHA_B, 'success', pr=9),
            linux_run(1, SHA_A, 'success', pr=9),
        ]
        self.assertEqual(incident.pr_range(runs, SHA_A, SHA_B), [9])
        self.assertEqual(incident.classify_incident(runs)[0], incident.UNKNOWN)

    def test_full_pass_must_have_every_trusted_job(self):
        bad_jobs = [{'name': name, 'conclusion': 'success'}
                    for name in incident.JOBS]
        bad_jobs[0]['conclusion'] = 'failure'
        runs = [
            mac_run(2, SHA_B, 'success', jobs=bad_jobs),
            mac_run(1, SHA_A, 'success'),
        ]
        self.assertEqual(incident.trusted_pass(runs), SHA_A)

    def test_commit_and_pr_range(self):
        runs = [
            mac_run(3, SHA_C, 'failure'),
            linux_run(2, SHA_B, 'success', pull_requests=[{'number': 12}]),
            mac_run(1, SHA_A, 'success', pr=11),
        ]
        self.assertEqual(incident.commit_range(runs, SHA_A, SHA_C), [SHA_C, SHA_B])
        self.assertEqual(incident.pr_range(runs, SHA_A, SHA_C), [12])
        self.assertEqual(incident.range_between(runs, SHA_A, SHA_C),
                         {'commits': [SHA_C, SHA_B], 'prs': [12]})


if __name__ == '__main__':
    unittest.main()
