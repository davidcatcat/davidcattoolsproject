"""#1706 R1: pure main CI incident classification (no network, no GitHub writes)."""

from __future__ import annotations

import copy
import unittest

from llmtool import mac_full_due
from llmtool import main_incident as mi


A, B, C, D = 'a' * 40, 'b' * 40, 'c' * 40, 'd' * 40
T = 'tests.test_sample.SampleCase.test_one'
U = 'tests.test_sample.SampleCase.test_two'


def linux(run_id, sha, conclusion='success', **extra):
    run = {'id': run_id, 'name': mi.LINUX_CHECK, 'head_sha': sha, 'status': 'completed',
           'conclusion': conclusion}
    run.update(extra)
    return run


def full(run_id, sha, conclusion='success', *, event='workflow_dispatch', branch='main',
         jobs=None, **extra):
    job = 'success' if conclusion == 'success' else 'failure'
    if jobs is None:
        jobs = [{'name': name, 'conclusion': job} for name in mac_full_due.JOBS]
    run = {'id': run_id, 'event': event, 'head_branch': branch, 'head_sha': sha,
           'status': 'completed', 'conclusion': conclusion, 'jobs': jobs}
    run.update(extra)
    return run


def docs(run_id, sha, conclusion='success'):
    return {'id': run_id, 'name': 'check (docs)', 'head_sha': sha, 'status': 'completed',
            'conclusion': conclusion}


def runs(*items):
    """Newest first: a higher id is a newer run."""
    return sorted(items, key=lambda run: run['id'], reverse=True)


def commits(*shas, changed=None):
    """Newest first; PR number is the position from oldest (A=1)."""
    changed = changed or {}
    total = len(shas)
    return [{'sha': sha, 'pr': total - index, 'changed': changed.get(sha, True)}
            for index, sha in enumerate(shas)]


def codes(evidence):
    return [item['code'] for item in evidence]


class RunKindTests(unittest.TestCase):
    def test_linux_by_exact_name_and_docs_is_not_linux(self):
        self.assertEqual(mi.run_kind(linux(1, A)), mi.LINUX)
        self.assertIsNone(mi.run_kind(docs(2, A)))

    def test_full_mac_needs_dispatch_on_main_and_the_full_job_set(self):
        self.assertEqual(mi.run_kind(full(1, A)), mi.FULL_MAC)
        self.assertIsNone(mi.run_kind(full(2, A, event='pull_request')))
        self.assertIsNone(mi.run_kind(full(3, A, branch='feature')))
        partial = [{'name': 'check (macOS 3.13)', 'conclusion': 'success'}]
        self.assertIsNone(mi.run_kind(full(4, A, jobs=partial)))
        self.assertIsNone(mi.run_kind(full(5, A, jobs=None) | {'jobs': 'bad'}))

    def test_a_mac_name_alone_is_never_full(self):
        named = {'id': 1, 'name': 'llmtool Mac CI FULL', 'head_sha': A, 'status': 'completed',
                 'conclusion': 'success', 'event': 'pull_request'}
        self.assertIsNone(mi.run_kind(named))
        self.assertIsNone(mi.run_kind(full(2, A, event='pull_request', name='mac full')))

    def test_non_dict_or_bad_identity_is_ignored(self):
        for value in (None, 'run', 7, [linux(1, A)]):
            self.assertIsNone(mi.run_kind(value))
        self.assertIsNone(mi.run_kind(linux('1', A)))
        self.assertIsNone(mi.run_kind(linux(1, 'not-a-sha')))


class HeadStateTests(unittest.TestCase):
    def test_trusted_pass_needs_linux_and_trusted_full(self):
        self.assertTrue(mi.trusted_pass(runs(linux(1, A), full(2, A)), A))
        self.assertFalse(mi.trusted_pass(runs(linux(1, A)), A))
        self.assertFalse(mi.trusted_pass(runs(full(2, A)), A))

    def test_docs_check_is_not_the_linux_check(self):
        self.assertFalse(mi.trusted_pass(runs(docs(1, A), full(2, A)), A))

    def test_pr_scoped_mac_run_is_never_full_trust(self):
        self.assertFalse(mi.trusted_pass(runs(linux(1, A), full(2, A, event='pull_request')), A))

    def test_full_success_without_complete_job_data_is_not_trusted(self):
        jobs = [{'name': name, 'conclusion': 'success'} for name in mac_full_due.JOBS]
        jobs[0] = {'name': jobs[0]['name'], 'conclusion': 'skipped'}
        self.assertFalse(mi.trusted_pass(runs(linux(1, A), full(2, A, jobs=jobs)), A))

    def test_trusted_pass_uses_the_newest_run(self):
        self.assertFalse(mi.trusted_pass(runs(linux(1, A), full(2, A), linux(3, A, 'failure')), A))
        self.assertTrue(mi.trusted_pass(runs(linux(1, A, 'failure'), full(2, A), linux(3, A)), A))

    def test_failed_run_then_green_rerun_on_same_sha_is_not_failing(self):
        self.assertFalse(mi.head_failed(runs(full(1, A, 'failure'), full(2, A)), A))
        self.assertTrue(mi.head_failed(runs(full(1, A), full(2, A, 'failure')), A))
        self.assertFalse(mi.head_failed(runs(linux(1, A, 'failure'), linux(2, A)), A))

    def test_newest_first_false_reads_the_list_oldest_first(self):
        oldest_first = [full(1, A, 'failure'), full(2, A)]
        self.assertFalse(mi.head_failed(oldest_first, A, newest_first=False))
        self.assertTrue(mi.head_failed(oldest_first, A, newest_first=True))

    def test_docs_or_pr_mac_failure_does_not_fail_a_head(self):
        self.assertFalse(mi.head_failed(runs(docs(1, A, 'failure'),
                                             full(2, A, 'failure', event='pull_request')), A))


class HeadsAndRangeTests(unittest.TestCase):
    def test_finds_last_trusted_pass_and_failing_head_and_range(self):
        history = commits(D, C, B, A)
        data = runs(linux(1, A), full(2, A), linux(3, C, 'failure', failed_tests=[T]))
        self.assertEqual(mi.find_heads(history, data), (A, C))
        self.assertEqual(mi.commit_range(history, A, C),
                         [{'sha': B, 'pr': 2}, {'sha': C, 'pr': 3}])

    def test_range_is_none_when_unknown_or_reversed(self):
        history = commits(C, B, A)
        self.assertIsNone(mi.commit_range(history, None, C))
        self.assertIsNone(mi.commit_range(history, C, A))
        self.assertIsNone(mi.commit_range(history, D, C))

    def test_newer_trusted_pass_means_no_open_incident(self):
        history = commits(B, A)
        data = runs(linux(1, A, 'failure'), linux(2, B), full(3, B))
        self.assertEqual(mi.find_heads(history, data), (B, None))

    def test_no_failing_head(self):
        result = mi.classify_incident(commits(B, A), runs(linux(1, A), full(2, A)))
        self.assertFalse(result['incident'])
        self.assertEqual((result['class'], result['fail_sha'], result['pass_sha']),
                         (mi.UNKNOWN, None, A))
        self.assertEqual(codes(result['evidence']), ['NO_FAILING_HEAD'])


class ClassifyTests(unittest.TestCase):
    def incident(self, data, history=None, **kwargs):
        return mi.classify_incident(history or commits(B, A),
                                    runs(linux(1, A), full(2, A), *data), **kwargs)

    def test_end_to_end_env_from_cancelled_full(self):
        result = self.incident([linux(10, B), full(11, B, 'cancelled')])
        self.assertTrue(result['incident'])
        self.assertEqual((result['pass_sha'], result['fail_sha']), (A, B))
        self.assertEqual(result['range'], [{'sha': B, 'pr': 2}])
        self.assertEqual(result['class'], mi.ENV)
        self.assertIn('ENV_CONCLUSION', codes(result['evidence']))
        self.assertIn({'code': 'RANGE', 'commits': 1, 'prs': [2]}, result['evidence'])

    def test_env_from_failed_setup_step(self):
        result = self.incident([linux(10, B, 'failure', failed_step='Set up Python')])
        self.assertEqual(result['class'], mi.ENV)
        self.assertIn('ENV_SETUP_STEP', codes(result['evidence']))

    def test_failed_test_step_is_not_env(self):
        result = self.incident([linux(10, B, 'failure', failed_step='Run tests')])
        self.assertEqual(result['class'], mi.UNKNOWN)
        self.assertNotIn('ENV_SETUP_STEP', codes(result['evidence']))

    def test_missing_or_unparsable_test_id_is_unknown_not_env(self):
        for extra in ({}, {'failed_tests': []}, {'failed_tests': [None]},
                      {'failed_tests': 'not a list'}, {'failed_tests': [T, '  ']}):
            with self.subTest(extra=extra):
                result = self.incident([linux(10, B, 'failure', **extra)])
                self.assertEqual(result['class'], mi.UNKNOWN)
                self.assertIn('TEST_ID_UNAVAILABLE', codes(result['evidence']))

    def test_unexplained_failure_next_to_a_cancel_is_not_env(self):
        result = self.incident([linux(10, B, 'failure'), full(11, B, 'cancelled')])
        self.assertEqual(result['class'], mi.UNKNOWN)

    def test_probe_successful_run_step_name_is_not_env(self):
        result = self.incident([linux(10, B, step_name='Checkout', reason='runner capacity'),
                                full(11, B, 'failure', failed_tests=[T])])
        self.assertEqual(result['class'], mi.UNKNOWN)
        self.assertFalse({'ENV_SETUP_STEP', 'ENV_REASON'} & set(codes(result['evidence'])))

    def test_capacity_cancel_that_is_not_a_failure_gives_no_env(self):
        quiet = linux(10, B, 'neutral', reason='Cancelled: runner capacity')
        self.assertEqual(mi._env_evidence(mi.LINUX, quiet), [])
        self.assertEqual(mi._env_evidence(mi.LINUX, linux(11, B, reason='runner capacity')), [])
        failed = linux(12, B, 'failure', reason='The runner lost communication with the server')
        self.assertEqual(codes(mi._env_evidence(mi.LINUX, failed)), ['ENV_REASON'])

    def test_code_from_linux_and_full_at_one_sha(self):
        result = self.incident([linux(10, B, 'failure', failed_tests=[T]),
                                full(11, B, 'failure', failed_tests=[T, U])])
        self.assertEqual(result['class'], mi.CODE)
        self.assertIn({'code': 'REPRODUCED', 'test': T, 'runs': [10, 11]}, result['evidence'])

    def test_code_from_two_full_runs(self):
        result = self.incident([linux(10, B), full(11, B, 'failure', failed_tests=[T]),
                                full(12, B, 'failure', failed_tests=[T])])
        self.assertEqual(result['class'], mi.CODE)

    def test_probe_cancelled_run_does_not_override_code(self):
        result = self.incident([linux(10, B, 'failure', failed_tests=[T]),
                                full(11, B, 'failure', failed_tests=[T]),
                                full(12, B, 'cancelled', reason='superseded')])
        self.assertEqual(result['class'], mi.CODE)
        self.assertIn('ENV_CONCLUSION', codes(result['evidence']))
        two_full = self.incident([linux(10, B, 'cancelled'), full(11, B, 'failure', failed_tests=[T]),
                                  full(12, B, 'failure', failed_tests=[T])])
        self.assertEqual(two_full['class'], mi.CODE)

    def test_pr_scoped_mac_failure_is_never_full_reproduction(self):
        result = self.incident([linux(10, B, 'failure', failed_tests=[T]),
                                full(11, B, 'failure', event='pull_request', failed_tests=[T])])
        self.assertEqual(result['class'], mi.UNKNOWN)
        mixed = self.incident([linux(10, B), full(11, B, 'failure', failed_tests=[T]),
                               full(12, B, 'failure', event='pull_request', failed_tests=[T])])
        self.assertEqual(mixed['class'], mi.UNKNOWN)

    def test_different_tests_are_not_reproduction(self):
        result = self.incident([linux(10, B, 'failure', failed_tests=[T]),
                                full(11, B, 'failure', failed_tests=[U])])
        self.assertEqual(result['class'], mi.UNKNOWN)

    def test_failure_before_a_green_rerun_is_not_reproduction(self):
        result = mi.classify(B, commits(B, A), runs(full(10, B, 'failure', failed_tests=[T]),
                                                   full(11, B),
                                                   linux(12, B, 'failure', failed_tests=[T])))
        self.assertEqual(result[0], mi.UNKNOWN)

    def test_one_pr_in_range_alone_is_not_code(self):
        result = self.incident([full(10, B), linux(11, B, 'failure', failed_tests=[T])])
        self.assertEqual(result['class'], mi.UNKNOWN)
        self.assertEqual(result['range'], [{'sha': B, 'pr': 2}])

    def flaky_case(self, changed=False, later=None):
        history = commits(C, B, A, changed={C: changed})
        later = [full(20, C)] if later is None else later
        return mi.classify_incident(history, runs(linux(1, A), full(2, A), linux(10, B),
                                                  full(11, B, 'failure', failed_tests=[T]),
                                                  *later))

    def test_flaky_when_a_newer_run_passes_on_a_later_unchanged_sha(self):
        result = self.flaky_case()
        self.assertEqual((result['pass_sha'], result['fail_sha']), (A, B))
        self.assertEqual(result['class'], mi.FLAKY)
        self.assertIn({'code': 'LATER_PASS', 'kind': mi.FULL_MAC, 'run': 20, 'sha': C,
                       'tests': [T]}, result['evidence'])

    def test_not_flaky_when_a_change_lies_between(self):
        self.assertEqual(self.flaky_case(changed=True)['class'], mi.UNKNOWN)

    def test_not_flaky_when_the_later_run_fails_a_different_test(self):
        # T failed on B; the newer run on unchanged C failed U, so T has no later pass.
        history = commits(C, B, A, changed={C: False})
        data = runs(linux(1, A), full(2, A), linux(10, B),
                    full(11, B, 'failure', failed_tests=[T]),
                    full(20, C, 'failure', failed_tests=[U]))
        result = mi.classify(B, history, data)
        self.assertEqual(result[0], mi.UNKNOWN)
        self.assertNotIn('LATER_PASS', codes(result[1]))

    def test_not_flaky_when_the_pass_is_an_older_run(self):
        self.assertEqual(self.flaky_case(later=[full(5, C)])['class'], mi.UNKNOWN)

    def test_not_flaky_from_a_newer_run_on_an_older_sha(self):
        # Codex v1 F1 on PR #1716: B fails, then a newer FULL run passes on the older
        # A. With B unchanged, reading history backwards would wrongly see FLAKY.
        history = commits(C, B, A, changed={B: False, C: False})
        data = runs(linux(10, B), full(11, B, 'failure', failed_tests=[T]), full(20, A))
        result = mi.classify(B, history, data)
        self.assertEqual(result[0], mi.UNKNOWN)
        self.assertNotIn('LATER_PASS', codes(result[1]))

    def test_later_sha_must_follow_the_failing_sha_in_history(self):
        chain = commits(C, B, A, changed={B: False, C: False})[::-1]
        self.assertTrue(mi._unchanged_after(chain, B, C))
        self.assertTrue(mi._unchanged_after(chain, A, C))
        self.assertFalse(mi._unchanged_after(chain, B, A))
        self.assertFalse(mi._unchanged_after(chain, C, A))
        self.assertFalse(mi._unchanged_after(chain, B, B))
        self.assertFalse(mi._unchanged_after(chain, B, D))
        self.assertFalse(mi._unchanged_after(chain, D, B))

    def test_not_flaky_when_only_another_check_passes(self):
        self.assertEqual(self.flaky_case(later=[linux(20, C)])['class'], mi.UNKNOWN)

    def test_not_flaky_from_a_pass_on_the_same_sha(self):
        failing = full(11, B, 'failure', failed_tests=[T])
        data = runs(linux(10, B, 'failure', failed_tests=[U]), failing, full(20, B))
        # The green FULL rerun on B ends the FULL failure and is not a later pass on
        # another SHA; the Linux failure of U has no later Linux pass.
        result = mi.classify(B, commits(C, B, A, changed={C: False}), data)
        self.assertEqual(result[0], mi.UNKNOWN)
        self.assertNotIn('LATER_PASS', codes(result[1]))

    def test_reproduced_and_later_unchanged_pass_is_a_conflict(self):
        history = commits(C, B, A, changed={C: False})
        data = runs(linux(1, A), full(2, A), full(10, B, 'failure', failed_tests=[T]),
                    full(11, B, 'failure', failed_tests=[T]), linux(12, B), full(20, C))
        result = mi.classify_incident(history, data)
        self.assertEqual(result['class'], mi.UNKNOWN)
        self.assertIn('CONFLICT', codes(result['evidence']))

    def test_newest_first_false_gives_the_same_result(self):
        history = commits(C, B, A, changed={C: False})
        data = runs(linux(1, A), full(2, A), linux(10, B),
                    full(11, B, 'failure', failed_tests=[T]), full(20, C))
        expected = mi.classify_incident(history, data)
        self.assertEqual(expected['class'], mi.FLAKY)
        self.assertEqual(mi.classify_incident(history[::-1], data[::-1], newest_first=False),
                         expected)

    def test_non_dict_inputs_are_ignored(self):
        for history, data in ((None, None), ('commits', 'runs'), ([None, 3, 'x'], [None, 'x', 4]),
                              ({'sha': A}, {'id': 1})):
            with self.subTest(history=history, data=data):
                result = mi.classify_incident(history, data)
                self.assertFalse(result['incident'])
                self.assertEqual(result['class'], mi.UNKNOWN)
        mixed = mi.classify_incident([None, *commits(B, A), 'x'],
                                     [7, *runs(linux(1, A), full(2, A), full(3, B, 'cancelled')), None])
        self.assertEqual((mixed['fail_sha'], mixed['class']), (B, mi.ENV))

    def test_inputs_are_not_mutated(self):
        history = commits(C, B, A, changed={C: False})
        data = runs(linux(1, A), full(2, A), linux(10, B, 'failure', failed_tests=[T]),
                    full(11, B, 'failure', failed_tests=[T]), full(12, B, 'cancelled'),
                    full(20, C))
        before = copy.deepcopy((history, data))
        result = mi.classify_incident(history, data)
        self.assertEqual((history, data), before)
        result['range'][0]['pr'] = 99
        self.assertEqual((history, data), before)

    def test_classify_with_no_failing_run_is_unknown(self):
        self.assertEqual(mi.classify(B, commits(B, A), runs(linux(1, B))),
                         (mi.UNKNOWN, [{'code': 'NO_FAILING_RUN', 'sha': B}]))


if __name__ == '__main__':
    unittest.main()
