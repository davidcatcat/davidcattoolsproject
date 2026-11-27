"""#2125 R1: main_ci_job_summary.summarize reports every completed failing job."""
import copy
import unittest

from llmtool import main_ci_job_summary as summary
from llmtool import main_incident

SHA = 'a' * 40
OTHER = 'b' * 40
GATEWAY = 'gateway tests (Linux 3.14)'
CHECK = 'check (Linux 3.14)'


def job(jid, name, conclusion='success', status='completed', sha=SHA):
    return {'id': jid, 'name': name, 'head_sha': sha, 'status': status,
            'conclusion': conclusion}


class MainCiJobSummaryTests(unittest.TestCase):
    def test_mixed_results_two_pages_is_red(self):
        # The #2125 case: a failing non-required job and a cancelled required
        # job across two pages; both are listed with name, conclusion and id.
        pages = [[job(1, GATEWAY, 'failure'), job(2, 'lint', 'success')],
                 [job(3, CHECK, 'cancelled'), job(4, 'build', 'success')]]
        result = summary.summarize(SHA, pages, 4)
        self.assertEqual(result['state'], summary.RED)
        self.assertIsNone(result['reason'])
        self.assertEqual(len(result['failing']), 2)
        self.assertIn({'id': 1, 'name': GATEWAY, 'conclusion': 'failure'},
                      result['failing'])
        self.assertIn({'id': 3, 'name': CHECK, 'conclusion': 'cancelled'},
                      result['failing'])

    def test_all_success_is_green(self):
        pages = [[job(1, GATEWAY), job(2, 'lint', 'skipped'), job(3, 'build', 'neutral')]]
        result = summary.summarize(SHA, pages, 3)
        self.assertEqual(result['state'], summary.GREEN)
        self.assertIsNone(result['reason'])
        self.assertEqual(result['failing'], [])

    def test_in_progress_job_is_unknown(self):
        pages = [[job(1, GATEWAY), job(2, 'lint', status='in_progress', conclusion=None)]]
        result = summary.summarize(SHA, pages, 2)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.JOB_NOT_COMPLETED)

    def test_missing_page_fewer_jobs_than_total_is_unknown(self):
        pages = [[job(1, GATEWAY), job(2, 'lint')]]
        result = summary.summarize(SHA, pages, 3)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.COUNT_MISMATCH)

    def test_empty_job_list_is_unknown(self):
        result = summary.summarize(SHA, [], 0)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.COUNT_MISMATCH)

    def test_more_jobs_than_total_is_unknown(self):
        pages = [[job(1, GATEWAY), job(2, 'lint')]]
        result = summary.summarize(SHA, pages, 1)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.COUNT_MISMATCH)

    def test_duplicate_job_ids_with_matching_total_are_unknown(self):
        # R2: a stated total of 2 with two received jobs that share one job id
        # is still UNKNOWN: two copies are not two distinct jobs.
        pages = [[job(1, GATEWAY), job(1, GATEWAY)]]
        result = summary.summarize(SHA, pages, 2)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.DUPLICATE_JOB_ID)

    def test_duplicate_job_ids_are_detected_when_count_mismatches(self):
        # R2: a duplicate id is UNKNOWN whether or not the received count matches
        # the stated total, and the duplicate reason wins over count_mismatch.
        cases = [
            ([job(1, GATEWAY), job(1, GATEWAY)], 3),
            ([job(1, GATEWAY), job(1, GATEWAY), job(2, 'lint')], 2),
        ]
        for jobs, total in cases:
            with self.subTest(jobs=jobs, total=total):
                result = summary.summarize(SHA, [jobs], total)
                self.assertEqual(result['state'], summary.UNKNOWN)
                self.assertEqual(result['reason'], summary.DUPLICATE_JOB_ID)

    def test_duplicate_job_ids_across_pages_are_unknown(self):
        pages = [[job(1, GATEWAY)], [job(1, 'lint')]]
        result = summary.summarize(SHA, pages, 2)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.DUPLICATE_JOB_ID)

    def test_malformed_page_and_malformed_job_are_unknown(self):
        def broken(field, value):
            j = job(1, GATEWAY)
            j[field] = value
            return j

        def missing(field):
            j = job(1, GATEWAY)
            del j[field]
            return j

        bad_pages = [
            (('not a list', 1), summary.PAGES_MALFORMED),
            (([7], 1), summary.PAGES_MALFORMED),
            (([[None]], 1), summary.MALFORMED_JOB),
            (([[missing('id')]], 1), summary.MALFORMED_JOB),
            (([[broken('id', 'one')]], 1), summary.MALFORMED_JOB),
            (([[missing('name')]], 1), summary.MALFORMED_JOB),
            (([[broken('name', 7)]], 1), summary.MALFORMED_JOB),
            (([[broken('status', 7)]], 1), summary.MALFORMED_JOB),
            (([[broken('conclusion', 7)]], 1), summary.MALFORMED_JOB),
            (([[broken('head_sha', 123)]], 1), summary.SHA_MISMATCH),
        ]
        for args, reason in bad_pages:
            with self.subTest(args=args):
                result = summary.summarize(SHA, *args)
                self.assertEqual(result['state'], summary.UNKNOWN)
                self.assertEqual(result['reason'], reason)

    def test_malformed_job_does_not_hide_later_failing_job(self):
        # F1: a malformed job makes the result UNKNOWN, but a completed failing
        # job later in the same listing is still reported.
        pages = [[None, job(2, GATEWAY, 'failure')]]
        result = summary.summarize(SHA, pages, 2)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.MALFORMED_JOB)
        self.assertEqual(result['failing'],
                         [{'id': 2, 'name': GATEWAY, 'conclusion': 'failure'}])

    def test_nonpositive_job_id_is_malformed_and_not_reported_as_failing(self):
        # Zero or negative job IDs are malformed and must not be admitted to
        # failing even when status is completed and conclusion is failing.
        for bad_id in (0, -1):
            with self.subTest(job_id=bad_id):
                pages = [[job(bad_id, GATEWAY, 'failure')]]
                result = summary.summarize(SHA, pages, 1)
                self.assertEqual(result['state'], summary.UNKNOWN)
                self.assertEqual(result['reason'], summary.MALFORMED_JOB)
                self.assertEqual(result['failing'], [])
                self.assertEqual(result['total_jobs'], 0)

    def test_positive_failing_jobs_reported_alongside_nonpositive_job_id(self):
        # Positive failed job IDs before and after a nonpositive-ID job are
        # still reported in input order with state UNKNOWN.
        for bad_id in (0, -5):
            with self.subTest(job_id=bad_id):
                pages = [[job(1, CHECK, 'cancelled'),
                          job(bad_id, 'broken', 'failure'),
                          job(2, GATEWAY, 'failure')]]
                result = summary.summarize(SHA, pages, 3)
                self.assertEqual(result['state'], summary.UNKNOWN)
                self.assertEqual(result['reason'], summary.MALFORMED_JOB)
                self.assertEqual(
                    result['failing'],
                    [{'id': 1, 'name': CHECK, 'conclusion': 'cancelled'},
                     {'id': 2, 'name': GATEWAY, 'conclusion': 'failure'}])
                self.assertEqual(result['total_jobs'], 2)

    def test_malformed_page_does_not_hide_failing_job_on_another_page(self):
        # F1: a malformed page is skipped, so a failing job on a usable page is
        # still reported.
        pages = [[job(1, GATEWAY, 'failure')], None]
        result = summary.summarize(SHA, pages, 2)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.PAGES_MALFORMED)
        self.assertEqual(result['failing'],
                         [{'id': 1, 'name': GATEWAY, 'conclusion': 'failure'}])

    def test_job_on_another_sha_is_unknown(self):
        pages = [[job(1, GATEWAY), job(2, 'lint', sha=OTHER)]]
        result = summary.summarize(SHA, pages, 2)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.SHA_MISMATCH)

    def test_invalid_sha_is_unknown(self):
        for bad in ('xyz', 'a' * 41, 'A' * 40, 'a' * 40 + '\n', None, 7):
            with self.subTest(sha=bad):
                result = summary.summarize(bad, [job(1, GATEWAY)], 1)
                self.assertEqual(result['state'], summary.UNKNOWN)
                self.assertEqual(result['reason'], summary.SHA_INVALID)

    def test_sha_with_trailing_newline_is_not_green(self):
        # F2: Python's $ anchor matches before a final newline; the SHA must be
        # exactly 40 hex digits, even when a successful job carries it.
        bad = SHA + '\n'
        result = summary.summarize(bad, [[job(1, GATEWAY, sha=bad)]], 1)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.SHA_INVALID)

    def test_completed_job_without_conclusion_is_unknown(self):
        pages = [[job(1, GATEWAY), job(2, 'lint', conclusion=None)]]
        result = summary.summarize(SHA, pages, 2)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.COMPLETED_WITHOUT_CONCLUSION)

    def test_empty_required_fields_are_malformed_not_green(self):
        # A blank name or status is malformed data: it must never read GREEN.
        for field, value in (('name', ''), ('name', '   '), ('status', ''),
                             ('status', '  ')):
            with self.subTest(field=field, value=value):
                broken = job(1, GATEWAY)
                broken[field] = value
                result = summary.summarize(SHA, [[broken]], 1)
                self.assertEqual(result['state'], summary.UNKNOWN)
                self.assertEqual(result['reason'], summary.MALFORMED_JOB)

    def test_completed_job_with_blank_conclusion_is_unknown(self):
        for conclusion in ('', '   '):
            with self.subTest(conclusion=conclusion):
                result = summary.summarize(SHA, [[job(1, GATEWAY, conclusion)]], 1)
                self.assertEqual(result['state'], summary.UNKNOWN)
                self.assertEqual(result['reason'], summary.MALFORMED_JOB)

    def test_completed_job_with_unrecognized_conclusion_is_unknown(self):
        # F1: a well-formed but unrecognized conclusion is malformed data, not a
        # pass; one such job must never let the listing read GREEN.
        for conclusion in ('bogus', 'action_required', 'stale'):
            with self.subTest(conclusion=conclusion):
                result = summary.summarize(SHA, [[job(1, GATEWAY, conclusion)]], 1)
                self.assertEqual(result['state'], summary.UNKNOWN)
                self.assertEqual(result['reason'], summary.UNKNOWN_CONCLUSION)
                self.assertEqual(result['failing'], [])

    def test_unrecognized_conclusion_does_not_hide_failing_job(self):
        # F1: an unrecognized conclusion makes the result UNKNOWN, but a usable
        # completed failing job in the same listing is still reported.
        pages = [[job(1, GATEWAY, 'failure'), job(2, 'lint', 'bogus')]]
        result = summary.summarize(SHA, pages, 2)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.UNKNOWN_CONCLUSION)
        self.assertEqual(result['failing'],
                         [{'id': 1, 'name': GATEWAY, 'conclusion': 'failure'}])

    def test_every_known_non_failing_conclusion_is_green(self):
        # The allow-list is exactly success, skipped and neutral; anything else
        # outside main_incident.BAD is unrecognized, never a clean pass.
        self.assertEqual(
            summary._NOT_FAILING, frozenset({'success', 'skipped', 'neutral'}))
        for conclusion in sorted(summary._NOT_FAILING):
            with self.subTest(conclusion=conclusion):
                result = summary.summarize(SHA, [[job(1, GATEWAY, conclusion)]], 1)
                self.assertEqual(result['state'], summary.GREEN)
                self.assertIsNone(result['reason'])

    def test_stated_total_not_int_is_unknown(self):
        result = summary.summarize(SHA, [[job(1, GATEWAY)]], '1')
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.STATED_TOTAL_INVALID)

    def test_invalid_stated_total_still_reports_failing_job(self):
        # F1: an invalid stated_total must not return before the scan; a usable
        # completed failing job is still reported, with a real total_jobs.
        for stated in ('1', None, 1.0, True):
            with self.subTest(stated=stated):
                pages = [[job(1, GATEWAY, 'failure')]]
                result = summary.summarize(SHA, pages, stated)
                self.assertEqual(result['state'], summary.UNKNOWN)
                self.assertEqual(result['reason'], summary.STATED_TOTAL_INVALID)
                self.assertEqual(
                    result['failing'],
                    [{'id': 1, 'name': GATEWAY, 'conclusion': 'failure'}])
                self.assertEqual(result['total_jobs'], 1)

    def test_usable_failing_job_is_reported_on_every_unknown_reason(self):
        # Invariant: once the job listing is iterable, no UNKNOWN reason may
        # drop a usable completed failing job.
        cases = [
            (summary.STATED_TOTAL_INVALID,
             [[job(1, GATEWAY, 'failure')]], '1'),
            (summary.PAGES_MALFORMED,
             [[job(1, GATEWAY, 'failure')], None], 2),
            (summary.MALFORMED_JOB,
             [[None, job(1, GATEWAY, 'failure')]], 2),
            (summary.SHA_MISMATCH,
             [[job(1, GATEWAY, 'failure'), job(2, 'lint', sha=OTHER)]], 2),
            (summary.COMPLETED_WITHOUT_CONCLUSION,
             [[job(1, GATEWAY, 'failure'),
               job(2, 'lint', conclusion=None)]], 2),
            (summary.UNKNOWN_CONCLUSION,
             [[job(1, GATEWAY, 'failure'), job(2, 'lint', 'bogus')]], 2),
            (summary.COUNT_MISMATCH,
             [[job(1, GATEWAY, 'failure'), job(2, 'lint')]], 3),
            (summary.DUPLICATE_JOB_ID,
             [[job(1, GATEWAY, 'failure'), job(1, 'lint')]], 2),
            (summary.JOB_NOT_COMPLETED,
             [[job(1, GATEWAY, 'failure'),
               job(2, 'lint', status='queued', conclusion=None)]], 2),
        ]
        for reason, pages, stated in cases:
            with self.subTest(reason=reason):
                result = summary.summarize(SHA, pages, stated)
                self.assertEqual(result['state'], summary.UNKNOWN)
                self.assertEqual(result['reason'], reason)
                self.assertEqual(
                    result['failing'],
                    [{'id': 1, 'name': GATEWAY, 'conclusion': 'failure'}])

    def test_failing_jobs_still_reported_on_unknown(self):
        # A failing job followed by an in-progress job is still listed.
        pages = [[job(1, GATEWAY, 'failure'), job(2, 'lint', status='queued',
                    conclusion=None)]]
        result = summary.summarize(SHA, pages, 2)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.JOB_NOT_COMPLETED)
        self.assertEqual(result['failing'],
                         [{'id': 1, 'name': GATEWAY, 'conclusion': 'failure'}])

    def test_incomplete_listing_still_lists_failing_jobs(self):
        # A failing job in a listing that is shorter than the stated total is
        # still reported with an UNKNOWN result.
        pages = [[job(1, GATEWAY, 'failure'), job(2, 'lint')]]
        result = summary.summarize(SHA, pages, 3)
        self.assertEqual(result['state'], summary.UNKNOWN)
        self.assertEqual(result['reason'], summary.COUNT_MISMATCH)
        self.assertEqual(result['failing'],
                         [{'id': 1, 'name': GATEWAY, 'conclusion': 'failure'}])

    def test_input_not_mutated(self):
        pages = [[job(1, GATEWAY, 'failure'), job(2, 'lint', 'success')]]
        stated = 2
        snapshot = copy.deepcopy(pages)
        summary.summarize(SHA, pages, stated)
        self.assertEqual(pages, snapshot)
        self.assertEqual(stated, 2)

    def test_every_bad_conclusion_is_red(self):
        # The failing set is exactly main_incident.BAD.
        for conclusion in sorted(main_incident.BAD):
            with self.subTest(conclusion=conclusion):
                result = summary.summarize(
                    SHA, [[job(1, GATEWAY, conclusion)]], 1)
                self.assertEqual(result['state'], summary.RED)
                self.assertEqual(result['failing'],
                                 [{'id': 1, 'name': GATEWAY,
                                   'conclusion': conclusion}])

    def test_failing_set_is_main_incident_bad(self):
        # The module reuses main_incident.BAD rather than copying it.
        self.assertEqual(set(summary._FAILED), set(main_incident.BAD))

    def test_failing_set_is_the_same_bad_object(self):
        # Identity, not just equality: the exported BAD object is reused.
        self.assertIs(summary._FAILED, main_incident.BAD)


if __name__ == '__main__':
    unittest.main()
