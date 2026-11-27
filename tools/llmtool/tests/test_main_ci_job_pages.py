"""#2602: main_ci_job_pages turns fetched CI job pages into summary input."""
import copy
import unittest

from llmtool import main_ci_job_pages as pages_mod
from llmtool import main_ci_job_summary as summary

SHA = 'a' * 40
OTHER_SHA = 'b' * 40
RUN_ID = 123456789
RUN_ATTEMPT = 2
TOTAL = 3


def job(job_id, **overrides):
    item = {'id': job_id, 'name': 'job %d' % job_id, 'head_sha': SHA,
            'status': 'completed', 'conclusion': 'success',
            'run_id': RUN_ID, 'run_attempt': RUN_ATTEMPT}
    item.update(overrides)
    return item


def page(jobs, total_count=TOTAL):
    return {'total_count': total_count, 'jobs': jobs}


def collect(pages, sha=SHA, run_id=RUN_ID, run_attempt=RUN_ATTEMPT):
    return pages_mod.collect_pages(sha, run_id, run_attempt, pages)


class MainCiJobPagesTests(unittest.TestCase):
    def assertUnknown(self, result, reason):
        self.assertEqual(result['state'], pages_mod.UNKNOWN)
        self.assertEqual(result['reason'], reason)

    def test_two_consistent_pages_feed_summary_like_direct_input(self):
        first = [job(1), job(2)]
        second = [job(3, conclusion='failure')]
        fetched = [page(first, TOTAL), page(second, TOTAL)]
        result = collect(fetched)
        self.assertEqual(result['state'], pages_mod.OK)
        self.assertIsNone(result['reason'])
        self.assertEqual(result['sha'], SHA)
        self.assertEqual(result['stated_total'], TOTAL)
        self.assertEqual(result['pages'], [first, second])
        direct = summary.summarize(SHA, [first, second], TOTAL)
        indirect = summary.summarize(result['sha'], result['pages'],
                                     result['stated_total'])
        self.assertEqual(indirect, direct)
        self.assertEqual(indirect['state'], summary.RED)

    def test_input_is_not_mutated(self):
        fetched = [page([job(1), job(2)], TOTAL), page([job(3)], TOTAL)]
        snapshot = copy.deepcopy(fetched)
        collect(fetched)
        self.assertEqual(fetched, snapshot)

    def test_missing_page_is_unknown(self):
        self.assertUnknown(collect([page([job(1), job(2)], TOTAL)]),
                           pages_mod.MISSING_PAGE)
        self.assertUnknown(collect([]), pages_mod.MISSING_PAGE)

    def test_total_count_differing_between_pages_is_unknown(self):
        fetched = [page([job(1), job(2)], 2), page([job(3)], 3)]
        self.assertUnknown(collect(fetched), pages_mod.TOTAL_COUNT_MISMATCH)

    def test_total_count_differing_from_jobs_received_is_unknown(self):
        fetched = [page([job(1), job(2), job(3)], 2)]
        self.assertUnknown(collect(fetched), pages_mod.TOTAL_COUNT_MISMATCH)

    def test_duplicate_page_is_unknown(self):
        fetched = [page([job(1), job(2)], 2), page([job(1), job(2)], 2)]
        self.assertUnknown(collect(fetched), pages_mod.DUPLICATE_PAGE)

    def test_duplicate_empty_pages_are_unknown(self):
        # One valid job plus two identical empty pages is ambiguous, not GREEN.
        fetched = [page([job(1)], 1), page([], 1), page([], 1)]
        self.assertUnknown(collect(fetched), pages_mod.DUPLICATE_PAGE)

    def test_duplicate_job_id_is_unknown(self):
        fetched = [page([job(1), job(2)], TOTAL), page([job(2), job(3)], TOTAL)]
        self.assertUnknown(collect(fetched), pages_mod.DUPLICATE_JOB_ID)

    def test_head_sha_mismatch_is_unknown(self):
        fetched = [page([job(1, head_sha=OTHER_SHA)], 1)]
        self.assertUnknown(collect(fetched), pages_mod.SHA_MISMATCH)

    def test_different_run_id_is_unknown(self):
        fetched = [page([job(1, run_id=RUN_ID + 1)], 1)]
        self.assertUnknown(collect(fetched), pages_mod.RUN_ID_MISMATCH)

    def test_different_run_attempt_is_unknown(self):
        fetched = [page([job(1, run_attempt=RUN_ATTEMPT + 1)], 1)]
        self.assertUnknown(collect(fetched), pages_mod.RUN_ATTEMPT_MISMATCH)

    def test_malformed_pages_are_unknown(self):
        cases = [None, 'pages', [['not', 'a', 'dict']],
                 [{'total_count': '1', 'jobs': []}],
                 [{'total_count': 1, 'jobs': 'not a list'}]]
        for fetched in cases:
            with self.subTest(fetched=fetched):
                self.assertUnknown(collect(fetched), pages_mod.PAGES_MALFORMED)

    def test_malformed_jobs_are_unknown(self):
        cases = [[page(['not a dict'], 1)],
                 [page([{'id': 1}], 1)],
                 [page([job(1, name='')], 1)],
                 [page([job(1, status=None)], 1)],
                 [page([job(1, conclusion=7)], 1)],
                 [page([job(1, run_id=None)], 1)],
                 [page([job(1, run_attempt=None)], 1)]]
        for fetched in cases:
            with self.subTest(fetched=fetched):
                self.assertUnknown(collect(fetched), pages_mod.MALFORMED_JOB)

    def test_zero_and_negative_job_ids_are_unknown(self):
        cases = [[page([job(0)], 1)],
                 [page([job(-1)], 1)]]
        for fetched in cases:
            with self.subTest(fetched=fetched):
                self.assertUnknown(collect(fetched), pages_mod.MALFORMED_JOB)

    def test_invalid_identity_inputs_are_unknown(self):
        cases = [((None, RUN_ID, RUN_ATTEMPT), pages_mod.SHA_INVALID),
                 (('not-a-sha', RUN_ID, RUN_ATTEMPT), pages_mod.SHA_INVALID),
                 ((SHA, 'run', RUN_ATTEMPT), pages_mod.RUN_ID_INVALID),
                 ((SHA, RUN_ID, 'attempt'), pages_mod.RUN_ATTEMPT_INVALID)]
        for args, reason in cases:
            with self.subTest(args=args):
                self.assertUnknown(pages_mod.collect_pages(*args, [page([job(1)], 1)]),
                                   reason)


if __name__ == '__main__':
    unittest.main()
