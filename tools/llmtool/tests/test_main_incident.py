"""#1706 R1 and R2: pure main CI incident classification and candidate evidence.

No network, no Git, no GitHub writes.
"""

from __future__ import annotations

import copy
import math
import unittest

from llmtool import mac_full_due
from llmtool import main_incident as mi
from llmtool import main_incident_graph as graph


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


def pending(run, status='queued'):
    """A copy of run that has not completed yet (queued or in progress)."""
    return dict(run, status=status, conclusion=None)


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


class PendingRerunTests(unittest.TestCase):
    """Codex v2 F1 on PR #1716: the newest run is chosen before any status filter."""

    def test_pending_rerun_after_failure_is_not_failing(self):
        for status in ('queued', 'in_progress'):
            with self.subTest(status=status):
                data = runs(linux(1, A, 'failure'), pending(linux(2, A), status))
                self.assertFalse(mi.head_failed(data, A))
                self.assertEqual(mi.check_states(data, A), {mi.LINUX: mi.PENDING})
                data = runs(linux(1, A), full(2, A, 'failure'), pending(full(3, A), status))
                self.assertFalse(mi.head_failed(data, A))
                self.assertEqual(mi.check_states(data, A),
                                 {mi.LINUX: mi.PASS, mi.FULL_MAC: mi.PENDING})

    def test_pending_rerun_after_success_is_not_a_trusted_pass(self):
        for status in ('queued', 'in_progress'):
            with self.subTest(status=status):
                self.assertTrue(mi.trusted_pass(runs(linux(1, A), full(2, A)), A))
                linux_rerun = runs(linux(1, A), full(2, A), pending(linux(3, A), status))
                self.assertFalse(mi.trusted_pass(linux_rerun, A))
                self.assertFalse(mi.head_failed(linux_rerun, A))
                full_rerun = runs(linux(1, A), full(2, A), pending(full(3, A), status))
                self.assertFalse(mi.trusted_pass(full_rerun, A))
                self.assertFalse(mi.head_failed(full_rerun, A))
                self.assertEqual(mi.check_states(full_rerun, A),
                                 {mi.LINUX: mi.PASS, mi.FULL_MAC: mi.PENDING})

    def test_pending_order_follows_newest_first_flag(self):
        oldest_first = [linux(1, A, 'failure'), pending(linux(2, A))]
        self.assertFalse(mi.head_failed(oldest_first, A, newest_first=False))
        self.assertTrue(mi.head_failed(oldest_first[::-1], A, newest_first=False))
        self.assertEqual(mi.newest_runs(oldest_first, A, newest_first=False)[mi.LINUX]['id'], 2)

    def test_pass_and_fail_need_a_completed_status(self):
        # A stale conclusion on a run that has not completed is never PASS or FAIL.
        self.assertFalse(mi._passed(mi.LINUX, linux(1, A, status='in_progress')))
        self.assertFalse(mi._passed(mi.FULL_MAC, full(2, A, status='queued')))
        self.assertFalse(mi.head_failed(runs(linux(3, A, 'failure', status='in_progress')), A))
        self.assertFalse(mi.trusted_pass(runs(linux(1, A, status='in_progress'), full(2, A)), A))
        self.assertEqual(mi.check_state(mi.LINUX, linux(4, A, status='queued')), mi.PENDING)

    def test_other_completed_result_has_no_state(self):
        self.assertIsNone(mi.check_state(mi.LINUX, linux(1, A, 'neutral')))
        self.assertEqual(mi.check_state(mi.LINUX, linux(2, A, 'cancelled')), mi.FAIL)

    def test_pending_rerun_on_a_head_opens_no_incident(self):
        history = commits(B, A)
        data = runs(linux(1, A), full(2, A), linux(10, B), full(11, B, 'failure', failed_tests=[T]),
                    pending(full(12, B), 'in_progress'))
        self.assertEqual(mi.find_heads(history, data), (A, None))
        result = mi.classify_incident(history, data)
        self.assertFalse(result['incident'])
        self.assertEqual(codes(result['evidence']), ['NO_FAILING_HEAD'])

    def test_pending_rerun_on_the_pass_head_hides_that_pass(self):
        history = commits(B, A)
        data = runs(linux(1, A), full(2, A), pending(linux(3, A)),
                    linux(10, B, 'failure', failed_tests=[T]))
        self.assertEqual(mi.find_heads(history, data), (None, B))


class SharedNewestRuleTests(unittest.TestCase):
    """Codex v3 F1 on PR #1716: every read of runs takes the newest run per SHA and check."""

    @staticmethod
    def full_data(*later):
        """B fails FULL Mac test T; later runs follow."""
        return runs(linux(1, A), full(2, A), linux(10, B),
                    full(11, B, 'failure', failed_tests=[T]), *later)

    @staticmethod
    def linux_data(*later):
        """B fails Linux test T; later runs follow."""
        return runs(linux(1, A), full(2, A), linux(10, B, 'failure', failed_tests=[T]),
                    full(11, B), *later)

    def full_flaky(self, *later, history=None):
        history = history or commits(C, B, A, changed={C: False})
        return mi.classify_incident(history, self.full_data(*later))

    def linux_flaky(self, *later):
        return mi.classify_incident(commits(C, B, A, changed={C: False}),
                                    self.linux_data(*later))

    def test_newest_state_reads_the_newest_run_then_its_status(self):
        data = runs(linux(1, A), pending(linux(2, A)), full(3, A))
        run, state = mi.newest_state(data, A, mi.LINUX)
        self.assertEqual((run['id'], state), (2, mi.PENDING))
        self.assertEqual(mi.newest_state(data, A, mi.FULL_MAC)[1], mi.PASS)
        self.assertEqual(mi.newest_state(data, B, mi.LINUX), (None, None))

    def test_full_pending_newer_run_after_a_later_success_is_not_flaky(self):
        self.assertEqual(self.full_flaky(full(20, C))['class'], mi.FLAKY)
        for status in ('queued', 'in_progress'):
            with self.subTest(status=status):
                result = self.full_flaky(full(20, C), pending(full(21, C), status))
                self.assertEqual(result['class'], mi.UNKNOWN)
                self.assertNotIn('LATER_PASS', codes(result['evidence']))

    def test_linux_pending_newer_run_after_a_later_success_is_not_flaky(self):
        result = self.linux_flaky(linux(20, C))
        self.assertEqual((result['fail_sha'], result['class']), (B, mi.FLAKY))
        self.assertIn({'code': 'LATER_PASS', 'kind': mi.LINUX, 'run': 20, 'sha': C,
                       'tests': [T]}, result['evidence'])
        for status in ('queued', 'in_progress'):
            with self.subTest(status=status):
                result = self.linux_flaky(linux(20, C), pending(linux(21, C), status))
                self.assertEqual(result['class'], mi.UNKNOWN)
                self.assertNotIn('LATER_PASS', codes(result['evidence']))

    def test_pending_later_run_follows_the_newest_first_flag(self):
        history = commits(C, B, A, changed={C: False})
        data = runs(linux(1, A), full(2, A), linux(10, B, 'failure', failed_tests=[T]),
                    full(11, B), linux(20, C), pending(linux(21, C)))
        expected = mi.classify_incident(history, data)
        self.assertEqual(expected['class'], mi.UNKNOWN)
        self.assertEqual(mi.classify_incident(history[::-1], data[::-1], newest_first=False),
                         expected)

    def test_later_success_hidden_by_a_newer_failure_on_that_sha_is_not_flaky(self):
        # Classify B directly: C's newest run failed, so its older success is no pass.
        history = commits(C, B, A, changed={C: False})
        for data in (self.full_data(full(20, C), full(21, C, 'failure', failed_tests=[U])),
                     self.linux_data(linux(20, C), linux(21, C, 'failure', failed_tests=[U]))):
            with self.subTest(kind=mi.run_kind(data[0])):
                result = mi.classify(B, history, data)
                self.assertEqual(result[0], mi.UNKNOWN)
                self.assertNotIn('LATER_PASS', codes(result[1]))
                self.assertEqual(mi.classify(B, history, data[1:])[0], mi.FLAKY)

    def test_pending_sha_is_skipped_but_a_later_clean_sha_still_counts(self):
        history = commits(D, C, B, A, changed={C: False, D: False})
        result = self.full_flaky(full(20, C), pending(full(21, C)), full(22, D), history=history)
        self.assertEqual(result['class'], mi.FLAKY)
        self.assertIn({'code': 'LATER_PASS', 'kind': mi.FULL_MAC, 'run': 22, 'sha': D,
                       'tests': [T]}, result['evidence'])

    def test_pending_rerun_on_the_failing_sha_supplies_no_failing_runs(self):
        history = commits(B, A)
        failing = [linux(10, B, 'failure', failed_tests=[T]),
                   full(11, B, 'failure', failed_tests=[T])]
        self.assertEqual(mi.classify_incident(history, runs(linux(1, A), full(2, A),
                                                            *failing))['class'], mi.CODE)
        for status in ('queued', 'in_progress'):
            with self.subTest(status=status):
                # The FULL check is PENDING, so its older failure is no reproduction.
                data = runs(linux(1, A), full(2, A), *failing, pending(full(12, B), status))
                result = mi.classify_incident(history, data)
                self.assertEqual((result['fail_sha'], result['class']), (B, mi.UNKNOWN))
                self.assertNotIn('REPRODUCED', codes(result['evidence']))
                tested = [item['kind'] for item in result['evidence']
                          if item['code'] == 'TEST_FAILURE']
                self.assertEqual(tested, [mi.LINUX])

    def test_classify_on_a_sha_whose_only_failure_is_pending_has_no_failing_run(self):
        data = runs(linux(10, B, 'failure', failed_tests=[T]), pending(linux(11, B)))
        self.assertEqual(mi.classify(B, commits(B, A), data),
                         (mi.UNKNOWN, [{'code': 'NO_FAILING_RUN', 'sha': B}]))


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


# #1706 R2: candidate rows, attribution, bisect plan and later-merge dependency check.

LL = 'tools/llmtool/'
FAILING = 'tests.test_alpha.AlphaCase.test_one'
FAILING_MODULE = 'tests.test_alpha'
M1, M2, M3, M4, M5 = ('1' * 40, '2' * 40, '3' * 40, '4' * 40, '5' * 40)
CLOSURE = {'files': [LL + 'llmtool/__init__.py', LL + 'llmtool/alpha.py',
                     LL + 'tests/test_alpha.py']}


def merged(number, sha, files, task=True, **extra):
    item = {'pr': number, 'merge_sha': sha, 'files': list(files)}
    if task:
        item['task_id'] = 'task-%d' % number
    item.update(extra)
    return item


def own(signal, state, module=FAILING_MODULE):
    return {'signal': signal, 'modules': {module: state}}


def three_prs():
    """Design acceptance 5: three merges, only 102 touches the import closure."""
    return [merged(101, M1, ['docs/notes.md']),
            merged(102, M2, [LL + 'llmtool/alpha.py', LL + 'tests/test_alpha.py']),
            merged(103, M3, ['.github/workflows/ci.yml', LL + 'requirements-dev.txt'], task=False)]


def three_rows():
    return mi.candidate_rows(three_prs(), CLOSURE, [FAILING],
                             {101: own(mi.FULL_MAC, mi.PASS), 102: own(mi.FULL_MAC, mi.NOT_RUN)})


class CandidateRowTests(unittest.TestCase):
    def test_one_row_per_pr_with_flags_in_merge_order(self):
        rows = three_rows()
        self.assertEqual([(row['pr'], row['merge_sha']) for row in rows],
                         [(101, M1), (102, M2), (103, M3)])
        first, second, third = rows
        self.assertEqual((first['task_id'], first['touches_import_closure'],
                          first['touches_test_file'], first['touches_ci_or_env']),
                         ('task-101', False, False, False))
        self.assertEqual((first['own_ci'], first['own_ci_signal']), (mi.PASS, mi.FULL_MAC))
        self.assertTrue(second['touches_import_closure'])
        self.assertEqual(second['closure_files'],
                         [LL + 'llmtool/alpha.py', LL + 'tests/test_alpha.py'])
        self.assertEqual((second['touches_test_file'], second['test_files']),
                         (True, [LL + 'tests/test_alpha.py']))
        self.assertEqual((second['touches_ci_or_env'], second['own_ci']), (False, mi.NOT_RUN))
        self.assertEqual(second['own_ci_tests'], {FAILING: mi.NOT_RUN})
        self.assertEqual((third['task_id'], third['touches_ci_or_env']), (mi.UNKNOWN, True))
        self.assertEqual(third['ci_env_files'],
                         ['.github/workflows/ci.yml', LL + 'requirements-dev.txt'])
        self.assertEqual((third['own_ci'], third['own_ci_signal']), (mi.UNKNOWN, mi.UNKNOWN))
        self.assertEqual([row['train'] for row in rows], [False, False, False])

    def test_ci_or_env_paths(self):
        for path in ('.github/workflows/llmtool-mac-ci.yml', LL + 'requirements.txt',
                     'requirements-dev.in', 'Makefile', LL + 'Makefile',
                     LL + 'llmtool/worktree_env.py', LL + 'tests/test_worktree_env.py'):
            with self.subTest(path=path):
                self.assertTrue(mi._ci_or_env(path))
        for path in ('docs/workflows/notes.md', LL + 'llmtool/alpha.py', 'README.md',
                     LL + 'llmtool/requirements_check.py'):
            with self.subTest(path=path):
                self.assertFalse(mi._ci_or_env(path))

    def test_merge_train_pr_is_one_row_listing_its_member_tasks(self):
        train = merged(110, M1, [LL + 'llmtool/alpha.py'],
                       members=['task-a', {'pr': 98, 'task_id': 'task-b'}, {'pr': 'x'}])
        rows = mi.candidate_rows([train], CLOSURE, [FAILING], {})
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['pr'], rows[0]['train']), (110, True))
        self.assertEqual(rows[0]['members'], [{'pr': None, 'task_id': 'task-a'},
                                              {'pr': 98, 'task_id': 'task-b'},
                                              {'pr': None, 'task_id': mi.UNKNOWN}])

    def test_own_ci_reads_the_most_specific_module_and_keeps_three_states(self):
        other = 'tests.test_beta.BetaCase.test_two'
        modules = {'tests': mi.NOT_RUN, FAILING_MODULE: mi.PASS, 'tests.test_gamma': 'FAIL'}
        cases = (([FAILING], mi.PASS), ([FAILING, other], mi.NOT_RUN),
                 (['tests.test_gamma.Case.test_x'], mi.UNKNOWN), ([], mi.UNKNOWN))
        for tests, expected in cases:
            with self.subTest(tests=tests):
                row = mi.candidate_rows([merged(1, M1, [])], [], tests,
                                        {1: {'signal': mi.LINUX, 'modules': modules}})[0]
                self.assertEqual((row['own_ci'], row['own_ci_signal']), (expected, mi.LINUX))
        row = mi.candidate_rows([merged(1, M1, [])], [], [FAILING],
                                {1: {'signal': 'mac', 'modules': {FAILING_MODULE: mi.PASS}}})[0]
        self.assertEqual((row['own_ci'], row['own_ci_signal']), (mi.PASS, mi.UNKNOWN))
        missing = mi.candidate_rows([merged(1, M1, [])], [], [FAILING], {2: own(mi.LINUX, mi.PASS)})
        self.assertEqual(missing[0]['own_ci_tests'], {FAILING: mi.UNKNOWN})

    def test_unknown_changed_files_give_unknown_flags(self):
        for files in (None, 'alpha.py', [LL + 'llmtool/alpha.py', 3]):
            with self.subTest(files=files):
                row = mi.candidate_rows([{'pr': 5, 'merge_sha': M1, 'files': files}],
                                        CLOSURE, [FAILING], {})[0]
                self.assertIsNone(row['files'])
                self.assertEqual((row['touches_import_closure'], row['touches_test_file'],
                                  row['touches_ci_or_env']), (None, None, None))

    def test_test_package_init_is_not_the_failing_test_file(self):
        rows = mi.candidate_rows([merged(1, M1, [LL + 'tests/__init__.py']),
                                  merged(2, M2, [LL + 'tests/test_alphabet.py']),
                                  merged(3, M3, ['some/path.txt'])],
                                 [], [FAILING, 'some/path.txt'], {})
        self.assertEqual([row['touches_test_file'] for row in rows], [False, False, True])

    def test_closure_from_import_closure_flags_an_indirect_import(self):
        files = {LL + 'llmtool/__init__.py': '', LL + 'llmtool/alpha.py': 'from . import beta\n',
                 LL + 'llmtool/beta.py': 'VALUE = 1\n', LL + 'llmtool/other.py': 'VALUE = 2\n',
                 LL + 'tests/test_alpha.py': 'from llmtool import alpha\n'}
        closure = graph.import_closure(FAILING, files)
        rows = mi.candidate_rows([merged(1, M1, [LL + 'llmtool/beta.py']),
                                  merged(2, M2, [LL + 'llmtool/other.py'])],
                                 [closure], [FAILING], {})
        self.assertEqual([row['touches_import_closure'] for row in rows], [True, False])

    def test_closure_built_from_tool_relative_paths_matches_repository_paths(self):
        # import_closure keeps the path form of its map; Git reports repository paths.
        files = {'llmtool/__init__.py': '', './llmtool/alpha.py': 'from . import beta\n',
                 'llmtool/beta.py': 'VALUE = 1\n',
                 'tests/test_alpha.py': 'from llmtool import alpha\n'}
        closure = graph.import_closure(FAILING, files)
        self.assertIn('llmtool/beta.py', closure['files'])
        rows = mi.candidate_rows([merged(1, M1, [LL + 'llmtool/beta.py']),
                                  merged(2, M2, [LL + 'llmtool/alpha.py']),
                                  merged(3, M3, ['llmtool/beta.py'])],
                                 closure, [FAILING], {})
        self.assertEqual([row['touches_import_closure'] for row in rows], [True, True, False])

    def test_non_list_inputs_and_no_mutation(self):
        self.assertEqual(mi.candidate_rows(None, CLOSURE, [FAILING], {}), [])
        self.assertEqual(mi.candidate_rows([None, 'x'], CLOSURE, [FAILING], {}), [])
        prs, closure, owned = three_prs(), copy.deepcopy(CLOSURE), {102: own(mi.LINUX, mi.PASS)}
        before = copy.deepcopy((prs, closure, owned))
        rows = mi.candidate_rows(prs, closure, [FAILING], owned)
        rows[1]['files'].append('extra')
        rows[1]['own_ci_tests'][FAILING] = 'changed'
        self.assertEqual((prs, closure, owned), before)


def bisect_shas(count):
    return ['%040x' % (index + 1) for index in range(count)]


def run_bisect(shas, first):
    """Answer each next probe as if the merge at index first is the first failing one."""
    results = {}
    while True:
        plan = mi.bisect_plan(shas, results)
        pending = [sha for sha in plan if sha not in results]
        if not pending:
            return plan, results
        assert len(pending) == 1 and pending[0] == plan[-1]
        results[pending[0]] = mi.FAIL if shas.index(pending[0]) >= first else mi.PASS


class AttributionTests(unittest.TestCase):
    def test_three_pr_batch_with_one_closure_hit(self):
        result = mi.attribute(three_rows())
        self.assertEqual((result['attribution'], result['basis'], result['candidates']),
                         (mi.BATCH, 'MULTIPLE_CANDIDATES', 3))
        self.assertEqual([row['pr'] for row in result['rows']], [102, 103, 101])
        self.assertEqual([row['pr'] for row in result['rows'] if row['touches_import_closure']],
                         [102])
        # No BATCH field names one PR.
        self.assertEqual(set(result), {'attribution', 'basis', 'candidates', 'rows'})

    def test_one_pr_single(self):
        result = mi.attribute(three_rows()[1:2])
        self.assertEqual((result['attribution'], result['basis'], result['pr'],
                          result['merge_sha'], result['task_id'], result['evidence']),
                         (mi.SINGLE, 'ONLY_CANDIDATE', 102, M2, 'task-102', None))
        self.assertEqual([row['pr'] for row in result['rows']], [102])

    def test_bisect_single_names_the_first_failing_merge_with_evidence(self):
        shas = [M1, M2, M3]
        _, results = run_bisect(shas, 2)
        first = mi.bisect_first_failing(shas, results)
        self.assertEqual(first, M3)
        result = mi.attribute(three_rows(), {'first_failing_sha': first,
                                             'evidence': 'bisect probe record 77'})
        self.assertEqual((result['attribution'], result['basis'], result['pr'], result['evidence']),
                         (mi.SINGLE, 'BISECT', 103, 'bisect probe record 77'))

    def test_bisect_that_names_no_single_row_gives_batch(self):
        rows = three_rows()
        for bisect in ({'first_failing_sha': M4, 'evidence': 'record'},
                       {'first_failing_sha': M2},
                       {'first_failing_sha': M2, 'evidence': '  '},
                       {'first_failing_sha': 'short', 'evidence': 'record'},
                       {}, 'M2'):
            with self.subTest(bisect=bisect):
                result = mi.attribute(rows, bisect)
                self.assertEqual((result['attribution'], result['basis']),
                                 (mi.BATCH, 'BISECT_UNMATCHED'))
                self.assertNotIn('pr', result)
        one = mi.attribute(rows[:1], {'first_failing_sha': M4, 'evidence': 'record'})
        self.assertEqual(one['attribution'], mi.BATCH)
        twice = rows + [dict(rows[0], pr=104)]
        self.assertEqual(mi.attribute(twice, {'first_failing_sha': M1,
                                              'evidence': 'record'})['attribution'], mi.BATCH)

    def test_full_mac_failure_with_more_than_one_candidate_defaults_to_batch(self):
        owned = {101: own(mi.FULL_MAC, mi.NOT_RUN), 102: own(mi.FULL_MAC, mi.NOT_RUN)}
        rows = mi.candidate_rows(three_prs()[:2], CLOSURE, [FAILING], owned)
        self.assertEqual({row['own_ci_signal'] for row in rows}, {mi.FULL_MAC})
        result = mi.attribute(rows)
        self.assertEqual(result['attribution'], mi.BATCH)
        self.assertNotIn('pr', result)

    def test_unknown_files_rank_with_flagged_rows(self):
        rows = mi.candidate_rows([merged(1, M1, ['docs/notes.md']),
                                  {'pr': 2, 'merge_sha': M2, 'files': None}],
                                 CLOSURE, [FAILING], {})
        self.assertEqual([row['pr'] for row in mi.attribute(rows)['rows']], [2, 1])

    def test_no_candidates_is_batch(self):
        for rows in ([], None, 'rows', [None]):
            with self.subTest(rows=rows):
                result = mi.attribute(rows)
                self.assertEqual((result['attribution'], result['basis'], result['rows']),
                                 (mi.BATCH, 'NO_CANDIDATE', []))

    def test_attribute_does_not_mutate_or_alias_rows(self):
        rows = three_rows()
        before = copy.deepcopy(rows)
        result = mi.attribute(rows)
        result['rows'][0]['files'].append('extra')
        single = mi.attribute(rows[:1])
        single['rows'][0]['pr'] = 999
        self.assertEqual(rows, before)


class BisectPlanTests(unittest.TestCase):
    def test_limit_is_ceil_log2_plus_one(self):
        for count, limit in ((0, 0), (1, 1), (2, 2), (3, 3), (4, 3), (5, 4), (16, 5), (17, 6)):
            with self.subTest(count=count):
                self.assertEqual(mi.bisect_limit(count), limit)

    def test_plan_stays_within_the_limit_and_finds_the_first_failing_merge(self):
        for count in range(1, 34):
            shas = bisect_shas(count)
            for first in range(count):
                with self.subTest(count=count, first=first):
                    plan, results = run_bisect(shas, first)
                    self.assertLessEqual(len(plan), math.ceil(math.log2(count)) + 1)
                    self.assertEqual(plan[0], shas[-1])
                    self.assertEqual(len(set(plan)), len(plan))
                    self.assertEqual(mi.bisect_first_failing(shas, results), shas[first])

    def test_plan_without_results_starts_with_the_failing_head(self):
        shas = bisect_shas(5)
        self.assertEqual(mi.bisect_plan(shas), [shas[-1]])
        self.assertIsNone(mi.bisect_first_failing(shas, {}))

    def test_head_that_does_not_reproduce_stops_the_search(self):
        shas = bisect_shas(5)
        self.assertEqual(mi.bisect_plan(shas, {shas[-1]: mi.PASS}), [shas[-1]])
        self.assertIsNone(mi.bisect_first_failing(shas, {shas[-1]: mi.PASS}))

    def test_probe_without_a_result_is_the_next_probe(self):
        shas = bisect_shas(5)
        results = {shas[-1]: mi.FAIL, shas[2]: mi.PENDING}
        self.assertEqual(mi.bisect_plan(shas, results), [shas[-1], shas[2]])
        self.assertIsNone(mi.bisect_first_failing(shas, results))

    def test_invalid_input_gives_an_empty_plan(self):
        for value in (None, 'shas', [], [M1, M1], [M1, 'short'], [M1, None]):
            with self.subTest(value=value):
                self.assertEqual(mi.bisect_plan(value), [])
                self.assertIsNone(mi.bisect_first_failing(value, {}))

    def test_inputs_are_not_mutated(self):
        shas = bisect_shas(6)
        results = {shas[-1]: mi.FAIL}
        before = copy.deepcopy((shas, results))
        plan = mi.bisect_plan(shas, results)
        plan.append('extra')
        self.assertEqual((shas, results), before)


ALPHA = LL + 'llmtool/alpha.py'
NEW = LL + 'llmtool/newmod.py'
OLD = LL + 'llmtool/oldmod.py'
USER = LL + 'llmtool/user.py'


def scope():
    return {'files': {
        ALPHA: {'before': 'def run():\n    return 1\n',
                'after': 'def run():\n    return 1\n\n\ndef added():\n    return 2\n'},
        NEW: {'before': None, 'after': 'VALUE = 1\n'},
        OLD: {'before': 'VALUE = 0\n', 'after': None},
        'docs/notes.md': {'before': 'old', 'after': 'new'},
    }}


def later(number, sha, files):
    return {'pr': number, 'merge_sha': sha, 'files': files}


class DependencyCheckTests(unittest.TestCase):
    def check_one(self, files):
        return mi.dependency_check(scope(), [later(201, M1, files)])

    def test_later_merge_editing_a_scope_file(self):
        for path in (ALPHA, 'docs/notes.md'):
            with self.subTest(path=path):
                result = self.check_one({path: 'VALUE = 3\n' if path == ALPHA else 'text'})
                self.assertEqual(result['code'], mi.REVERT_DEPENDENT)
                self.assertEqual(result['dependents'],
                                 [{'pr': 201, 'merge_sha': M1, 'kinds': [mi.EDITS_SCOPE_FILE],
                                   'files': [path], 'modules': [], 'names': []}])

    def test_later_merge_importing_a_module_the_revert_deletes(self):
        result = self.check_one({USER: 'from . import newmod\n'})
        self.assertEqual(result['code'], mi.REVERT_DEPENDENT)
        dependent = result['dependents'][0]
        self.assertEqual((dependent['kinds'], dependent['modules']),
                         ([mi.IMPORTS_DELETED_MODULE], ['llmtool.newmod']))

    def test_later_merge_importing_a_module_the_scope_deletes(self):
        result = self.check_one({USER: 'import llmtool.oldmod\n'})
        dependent = result['dependents'][0]
        self.assertEqual((dependent['kinds'], dependent['modules']),
                         ([mi.IMPORTS_DELETED_MODULE], ['llmtool.oldmod']))

    def test_later_merge_using_a_name_the_scope_adds(self):
        for text in ('from llmtool.alpha import added\n',
                     'from llmtool import alpha as al\n\nal.added()\n',
                     'from .alpha import *\n'):
            with self.subTest(text=text):
                result = self.check_one({USER: text})
                self.assertEqual(result['code'], mi.REVERT_DEPENDENT)
                dependent = result['dependents'][0]
                self.assertEqual((dependent['kinds'], dependent['names']),
                                 ([mi.USES_ADDED_NAME], ['llmtool.alpha.added']))

    def test_unrelated_later_merge_is_no_dependency(self):
        result = self.check_one({USER: 'from llmtool.alpha import run\n', 'README.md': 'text',
                                 LL + 'llmtool/gone.py': None})
        self.assertEqual((result['code'], result['dependents'], result['unparsable']),
                         (mi.NO_DEPENDENT, [], []))

    def test_each_dependent_is_listed_in_merge_order(self):
        merges = [later(301, M1, {USER: 'from llmtool.alpha import run\n'}),
                  later(302, M2, {ALPHA: 'def run():\n    return 4\n',
                                  USER: 'from llmtool import newmod\nnewmod.VALUE\n'}),
                  later(303, M3, {USER: 'from llmtool.alpha import added\n'})]
        result = mi.dependency_check(scope(), merges)
        self.assertEqual(result['code'], mi.REVERT_DEPENDENT)
        self.assertEqual([(item['pr'], item['kinds']) for item in result['dependents']],
                         [(302, [mi.EDITS_SCOPE_FILE, mi.IMPORTS_DELETED_MODULE,
                                 mi.USES_ADDED_NAME]),
                          (303, [mi.USES_ADDED_NAME])])
        self.assertEqual(result['dependents'][0]['names'], ['llmtool.newmod.VALUE'])

    def test_unparsable_text_is_reported_and_never_clears_the_check(self):
        result = self.check_one({USER: 'def broken(:\n'})
        self.assertEqual(result['code'], mi.DEPENDENCY_UNKNOWN)
        self.assertEqual(result['unparsable'], [{'source': 'later', 'pr': 201, 'path': USER}])
        broken = scope()
        broken['files'][ALPHA]['after'] = 'def broken(:\n'
        result = mi.dependency_check(broken, [later(202, M2, {USER: 'import json\n'})])
        self.assertEqual(result['code'], mi.DEPENDENCY_UNKNOWN)
        self.assertEqual(result['unparsable'], [{'source': 'scope', 'pr': None, 'path': ALPHA}])
        dependent = mi.dependency_check(broken, [later(203, M3, {ALPHA: 'VALUE = 1\n'})])
        self.assertEqual(dependent['code'], mi.REVERT_DEPENDENT)

    def test_empty_or_invalid_scope_is_unknown(self):
        for value in (None, {}, {'files': 'x'}, {'files': {}}):
            with self.subTest(scope=value):
                result = mi.dependency_check(value, [later(201, M1, {USER: 'import json\n'})])
                self.assertEqual((result['code'], result['dependents']),
                                 (mi.DEPENDENCY_UNKNOWN, []))
        self.assertEqual(mi.dependency_check(scope(), None)['code'], mi.NO_DEPENDENT)

    def test_inputs_are_not_mutated(self):
        merges = [later(301, M1, {ALPHA: 'VALUE = 1\n', USER: 'from .alpha import added\n'})]
        data = scope()
        before = copy.deepcopy((data, merges))
        result = mi.dependency_check(data, merges)
        result['dependents'][0]['files'].append('extra')
        self.assertEqual((data, merges), before)


if __name__ == '__main__':
    unittest.main()
