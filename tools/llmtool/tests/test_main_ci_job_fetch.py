"""#2602 slice 3: main_ci_job_fetch reads failed main jobs through a fake reader."""
import unittest

from llmtool import main_ci_job_fetch as fetch
from llmtool import main_ci_job_pages as pages
from llmtool import main_ci_job_summary as summary
from llmtool.errors import LLMToolError

SHA = 'a' * 40
OLDER = 'b' * 40
LINUX_RUN = 101
MAC_RUN = 202
LINUX_ATTEMPT = 2
MAC_ATTEMPT = 1
GATEWAY = 'gateway tests (Linux 3.14)'
LINUX_JOB = 'check (Linux 3.14)'
MAC_JOB = 'check (macOS 3.13)'
MAC_SHARDS = ('shards (1/3)', 'shards (2/3)', 'shards (3/3)')


def run(run_id, sha=SHA, attempt=1, created_at='2026-11-20T00:00:00Z'):
    return {'id': run_id, 'head_sha': sha, 'run_attempt': attempt,
            'created_at': created_at,
            'status': 'completed', 'conclusion': 'success'}


def reader_too_large():
    """The same refusal task_completion._github_get raises above its 1 MiB cap."""
    error = LLMToolError('GitHub evidence is unavailable; details omitted')
    error.completion_code = fetch.READER_TOO_LARGE
    return error


def job(job_id, name, run_id, attempt, sha=SHA, conclusion='success',
        status='completed'):
    return {'id': job_id, 'name': name, 'run_id': run_id,
            'run_attempt': attempt, 'head_sha': sha,
            'status': status, 'conclusion': conclusion}


class FakeGitHub:
    """A read-only fake: every call is one GET path; unknown paths fail."""

    def __init__(self, sha=SHA, main_date=None):
        self.sha = sha
        self.main_date = main_date
        self.runs = {}
        self.pages = {}
        self.fail = set()
        self.too_large = set()
        self.calls = []

    def workflow(self, workflow, event, runs):
        self.runs[(workflow, event)] = list(runs)

    def page(self, run_id, page, total, jobs):
        self.pages[(run_id, page)] = {'total_count': total, 'jobs': list(jobs)}

    def __call__(self, path):
        self.calls.append(path)
        if path in self.fail:
            raise OSError('api down')
        if path in self.too_large:
            raise reader_too_large()
        if path == fetch.MAIN:
            commit = {'sha': self.sha}
            if self.main_date is not None:
                commit['commit'] = {'committer': {'date': self.main_date}}
            return {'commit': commit}
        for (workflow, event), runs in self.runs.items():
            if path == fetch.RUNS.format(workflow=workflow, event=event,
                                         per_page=fetch.RUNS_PER_PAGE):
                return {'workflow_runs': runs}
        for (run_id, page), data in self.pages.items():
            if path == fetch.JOBS.format(run_id=run_id, per_page=fetch.JOBS_PER_PAGE,
                                         page=page):
                return data
        raise KeyError(path)

    def jobs_path(self, run_id, page):
        return fetch.JOBS.format(run_id=run_id, per_page=fetch.JOBS_PER_PAGE,
                                 page=page)


def mac_full_jobs(run_id=MAC_RUN, attempt=MAC_ATTEMPT):
    """The FULL Mac shard jobs plus the check job, all successful."""
    jobs = [job(2 + index, name, run_id, attempt)
            for index, name in enumerate(MAC_SHARDS)]
    return jobs + [job(5, MAC_JOB, run_id, attempt)]


def mac_skip_jobs(run_id, attempt, first_id):
    """A dispatch whose full-due gate skipped the shards, as GitHub lists it."""
    return [job(first_id, 'select', run_id, attempt)] + [
        job(first_id + 1 + index, name, run_id, attempt, conclusion='skipped')
        for index, name in enumerate(MAC_SHARDS)] + [
        job(first_id + 4, MAC_JOB, run_id, attempt)]


def green_github():
    """Both relevant runs complete; the Mac run executed the FULL shards."""
    github = FakeGitHub()
    github.workflow(fetch.LINUX_WORKFLOW, 'push', [run(LINUX_RUN, attempt=LINUX_ATTEMPT)])
    github.workflow(fetch.MAC_WORKFLOW, 'workflow_dispatch',
                    [run(MAC_RUN, attempt=MAC_ATTEMPT)])
    github.page(LINUX_RUN, 1, 1, [job(1, LINUX_JOB, LINUX_RUN, LINUX_ATTEMPT)])
    github.page(MAC_RUN, 1, len(mac_full_jobs()), mac_full_jobs())
    return github


class MainCiJobFetchTests(unittest.TestCase):
    def test_pagination_reports_non_required_failing_job(self):
        github = FakeGitHub()
        github.workflow(fetch.LINUX_WORKFLOW, 'push', [run(LINUX_RUN, attempt=LINUX_ATTEMPT)])
        github.workflow(fetch.MAC_WORKFLOW, 'workflow_dispatch',
                        [run(MAC_RUN, attempt=MAC_ATTEMPT)])
        github.page(LINUX_RUN, 1, 3, [
            job(1, 'build', LINUX_RUN, LINUX_ATTEMPT),
            job(2, GATEWAY, LINUX_RUN, LINUX_ATTEMPT, conclusion='failure')])
        github.page(LINUX_RUN, 2, 3, [job(3, 'lint', LINUX_RUN, LINUX_ATTEMPT)])
        github.page(MAC_RUN, 1, len(mac_full_jobs()), mac_full_jobs())
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['sha'], SHA)
        self.assertEqual(result['state'], fetch.RED)
        self.assertIsNone(result['reason'])
        self.assertEqual(result['failing'], [{
            'kind': fetch.LINUX, 'workflow': fetch.LINUX_WORKFLOW,
            'run_id': LINUX_RUN, 'run_attempt': LINUX_ATTEMPT,
            'id': 2, 'name': GATEWAY, 'conclusion': 'failure'}])
        linux, mac = result['runs']
        self.assertEqual((linux['run_id'], linux['run_attempt'], linux['state']),
                         (LINUX_RUN, LINUX_ATTEMPT, fetch.RED))
        self.assertEqual((mac['run_id'], mac['run_attempt'], mac['state']),
                         (MAC_RUN, MAC_ATTEMPT, fetch.GREEN))
        # Each run's own total drove its own pages: no pooling across runs.
        self.assertIn(github.jobs_path(LINUX_RUN, 2), github.calls)
        self.assertNotIn(github.jobs_path(MAC_RUN, 2), github.calls)

    def test_complete_green_runs_are_green(self):
        result = fetch.inspect_jobs(green_github())
        self.assertEqual((result['state'], result['reason']), (fetch.GREEN, None))
        self.assertEqual(result['failing'], [])
        self.assertEqual([run['state'] for run in result['runs']],
                         [fetch.GREEN, fetch.GREEN])

    def test_mac_skip_run_selects_the_older_full_run(self):
        github = green_github()
        newer = MAC_RUN + 1
        github.workflow(fetch.MAC_WORKFLOW, 'workflow_dispatch', [
            run(newer, attempt=1), run(MAC_RUN, attempt=MAC_ATTEMPT)])
        github.page(newer, 1, 5, mac_skip_jobs(newer, 1, 20))
        result = fetch.inspect_jobs(github)
        self.assertEqual((result['state'], result['reason']), (fetch.GREEN, None))
        self.assertEqual((result['runs'][1]['run_id'],
                          result['runs'][1]['run_attempt']),
                         (MAC_RUN, MAC_ATTEMPT))

    def test_mac_skip_run_does_not_hide_an_older_full_failure(self):
        github = green_github()
        github.page(MAC_RUN, 1, 4, [
            job(2, MAC_SHARDS[0], MAC_RUN, MAC_ATTEMPT, conclusion='failure'),
            job(3, MAC_SHARDS[1], MAC_RUN, MAC_ATTEMPT),
            job(4, MAC_SHARDS[2], MAC_RUN, MAC_ATTEMPT),
            job(5, MAC_JOB, MAC_RUN, MAC_ATTEMPT)])
        newer = MAC_RUN + 1
        github.workflow(fetch.MAC_WORKFLOW, 'workflow_dispatch', [
            run(newer, attempt=1), run(MAC_RUN, attempt=MAC_ATTEMPT)])
        github.page(newer, 1, 5, mac_skip_jobs(newer, 1, 20))
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.RED)
        self.assertEqual(result['runs'][1]['run_id'], MAC_RUN)
        self.assertEqual(result['failing'], [{
            'kind': fetch.FULL_MAC, 'workflow': fetch.MAC_WORKFLOW,
            'run_id': MAC_RUN, 'run_attempt': MAC_ATTEMPT,
            'id': 2, 'name': MAC_SHARDS[0], 'conclusion': 'failure'}])

    def test_mac_skip_run_without_an_older_full_run_is_unknown(self):
        github = green_github()
        github.page(MAC_RUN, 1, 5, mac_skip_jobs(MAC_RUN, MAC_ATTEMPT, 20))
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.FULL_EVIDENCE_MISSING)
        self.assertEqual(result['runs'][1]['run_id'], MAC_RUN)
        self.assertEqual(result['failing'], [])

    def test_mac_skip_runs_past_the_scan_bound_are_unknown(self):
        github = green_github()
        newer_runs = []
        for offset in range(fetch.MAX_RUNS_TO_SCAN + 1):
            newer = MAC_RUN + 1 + offset
            newer_runs.append(run(newer, attempt=1))
            github.page(newer, 1, 5,
                        mac_skip_jobs(newer, 1, 1000 + offset * 10))
        github.workflow(fetch.MAC_WORKFLOW, 'workflow_dispatch',
                        newer_runs + [run(MAC_RUN, attempt=MAC_ATTEMPT)])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.RUN_SCAN_BOUND)
        self.assertEqual(result['runs'][1]['run_id'],
                         MAC_RUN + 1 + fetch.MAX_RUNS_TO_SCAN)

    def test_newer_pending_run_is_not_hidden_by_an_older_success(self):
        github = green_github()
        newer = LINUX_RUN + 1
        github.workflow(fetch.LINUX_WORKFLOW, 'push', [
            run(newer, attempt=1), run(LINUX_RUN, attempt=LINUX_ATTEMPT)])
        github.page(newer, 1, 1, [
            job(9, LINUX_JOB, newer, 1, status='in_progress', conclusion=None)])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], summary.JOB_NOT_COMPLETED)
        self.assertEqual(result['runs'][0]['run_id'], newer)
        self.assertNotIn(github.jobs_path(LINUX_RUN, 1), github.calls)

    def test_pending_run_status_is_unknown_even_with_successful_jobs(self):
        github = green_github()
        newer = LINUX_RUN + 1
        pending = run(newer, attempt=1)
        pending['status'] = 'in_progress'
        pending['conclusion'] = None
        github.workflow(fetch.LINUX_WORKFLOW, 'push', [
            pending, run(LINUX_RUN, attempt=LINUX_ATTEMPT)])
        github.page(newer, 1, 1, [job(9, LINUX_JOB, newer, 1)])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.RUN_PENDING)
        self.assertEqual(result['runs'][0]['run_id'], newer)
        self.assertEqual(result['runs'][1]['state'], fetch.GREEN)

    def test_newer_failing_run_is_not_hidden_by_an_older_success(self):
        github = green_github()
        newer = LINUX_RUN + 1
        github.workflow(fetch.LINUX_WORKFLOW, 'push', [
            run(newer, attempt=1), run(LINUX_RUN, attempt=LINUX_ATTEMPT)])
        github.page(newer, 1, 1, [
            job(9, GATEWAY, newer, 1, conclusion='failure')])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.RED)
        self.assertEqual(result['runs'][0]['run_id'], newer)
        self.assertEqual(result['failing'][0]['id'], 9)
        self.assertNotIn(github.jobs_path(LINUX_RUN, 1), github.calls)

    def test_missing_run_is_unknown(self):
        github = green_github()
        github.workflow(fetch.LINUX_WORKFLOW, 'push', [run(LINUX_RUN, sha=OLDER)])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.RUN_MISSING)
        self.assertIsNone(result['runs'][0]['run_id'])
        self.assertEqual(result['runs'][1]['state'], fetch.GREEN)

    def test_full_mac_absence_proved_by_an_older_newest_run(self):
        github = green_github()
        github.main_date = '2026-11-21T00:00:00Z'
        github.workflow(fetch.MAC_WORKFLOW, 'workflow_dispatch', [
            run(MAC_RUN - 1, sha=OLDER, created_at='2026-11-20T00:00:00Z')])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.FULL_MAC_NOT_RUN)
        self.assertEqual(result['runs'][1]['reason'], fetch.FULL_MAC_NOT_RUN)
        self.assertEqual(result['failing'], [])
        # The Linux path is unchanged, and its success cannot make it GREEN.
        self.assertEqual(result['runs'][0]['state'], fetch.GREEN)
        # The main commit date came from the existing branches/main read.
        self.assertEqual(github.calls.count(fetch.MAIN), 1)

    def test_full_mac_runs_newer_than_main_commit_stay_run_list_bound(self):
        github = green_github()
        github.main_date = '2026-11-21T00:00:00Z'
        github.workflow(fetch.MAC_WORKFLOW, 'workflow_dispatch', [
            run(MAC_RUN, sha=OLDER, created_at='2026-11-22T00:00:00Z')])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.RUN_LIST_BOUND)

    def test_full_mac_absence_with_missing_or_bad_dates_stays_bound(self):
        cases = (
            (None, '2026-11-20T00:00:00Z'),          # main date missing
            ('not-a-date', '2026-11-20T00:00:00Z'),  # main date unparseable
            ('2026-11-21T00:00:00Z', None),          # run date missing
            ('2026-11-21T00:00:00Z', 'not-a-date'),  # run date unparseable
            ('9999-12-31T23:59:59-01:00', '2026-11-20T00:00:00Z'),  # main date UTC overflow
            ('2026-11-21T00:00:00Z', '9999-12-31T23:59:59-01:00'),  # run date UTC overflow
            ('2026-11-21T00:00:00Z', '0001-01-01T00:00:00+01:00'),  # run date UTC underflow
            ('2026-11-21T00:00:00Z', '2026-11-21T00:00:00Z'),  # not earlier
        )
        for main_date, created_at in cases:
            with self.subTest(main_date=main_date, created_at=created_at):
                github = green_github()
                github.main_date = main_date
                listing = run(MAC_RUN, sha=OLDER)
                if created_at is None:
                    del listing['created_at']
                else:
                    listing['created_at'] = created_at
                github.workflow(fetch.MAC_WORKFLOW, 'workflow_dispatch',
                                [listing])
                result = fetch.inspect_jobs(github)
                self.assertEqual(result['state'], fetch.UNKNOWN)
                self.assertEqual(result['reason'], fetch.RUN_LIST_BOUND)

    def test_full_mac_empty_listing_stays_run_list_bound(self):
        github = green_github()
        github.main_date = '2026-11-21T00:00:00Z'
        github.workflow(fetch.MAC_WORKFLOW, 'workflow_dispatch', [])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.RUN_LIST_BOUND)

    def test_linux_missing_run_stays_run_missing_when_dates_could_prove_it(self):
        github = green_github()
        github.main_date = '2026-11-21T00:00:00Z'
        github.workflow(fetch.LINUX_WORKFLOW, 'push', [
            run(LINUX_RUN, sha=OLDER, created_at='2026-11-20T00:00:00Z')])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.RUN_MISSING)

    def test_failed_read_is_unknown_and_other_failure_still_reported(self):
        github = green_github()
        github.page(LINUX_RUN, 1, 1, [
            job(1, GATEWAY, LINUX_RUN, LINUX_ATTEMPT, conclusion='failure')])
        github.fail.add(github.jobs_path(MAC_RUN, 1))
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.PAGES_FAILED)
        self.assertEqual(result['runs'][1]['reason'], fetch.PAGES_FAILED)
        self.assertEqual(result['failing'], [{
            'kind': fetch.LINUX, 'workflow': fetch.LINUX_WORKFLOW,
            'run_id': LINUX_RUN, 'run_attempt': LINUX_ATTEMPT,
            'id': 1, 'name': GATEWAY, 'conclusion': 'failure'}])

    def test_job_page_bound_is_unknown(self):
        github = green_github()
        total = fetch.MAX_JOB_PAGES * fetch.JOBS_PER_PAGE + 1
        github.page(LINUX_RUN, 1, total, [
            job(1, GATEWAY, LINUX_RUN, LINUX_ATTEMPT, conclusion='failure')])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.PAGES_BOUND)
        # The first page's known failure is still reported.
        self.assertEqual(result['failing'][0]['id'], 1)

    def test_run_list_bound_is_unknown(self):
        github = green_github()
        github.workflow(fetch.LINUX_WORKFLOW, 'push', [
            run(1000 + index, sha=OLDER)
            for index in range(fetch.RUNS_PER_PAGE)])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.RUN_LIST_BOUND)
        self.assertIsNone(result['runs'][0]['run_id'])

    def test_run_list_page_size_stays_inside_the_reader_cap(self):
        # #2602: 100 runs per page was 1,393,333 bytes. At the observed
        # ~14 KiB per run, 20 per page is about 280 KiB, well under the
        # 1 MiB GitHub reader cap in task_completion._github_get.
        reader_cap = 1024 * 1024
        conservative_bytes_per_run = 16 * 1024
        self.assertLessEqual(fetch.RUNS_PER_PAGE, 20)
        self.assertLess(fetch.RUNS_PER_PAGE * conservative_bytes_per_run,
                        reader_cap)

    def test_too_large_run_listing_is_unknown_with_its_own_reason(self):
        github = green_github()
        github.too_large.add(fetch.RUNS.format(
            workflow=fetch.LINUX_WORKFLOW, event='push',
            per_page=fetch.RUNS_PER_PAGE))
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.RUN_LIST_TOO_LARGE)
        self.assertEqual(result['runs'][0]['reason'], fetch.RUN_LIST_TOO_LARGE)
        self.assertEqual(result['runs'][1]['state'], fetch.GREEN)

    def test_too_large_job_page_keeps_pages_failed(self):
        # #2602: only the run listing gets its own reason; job pages keep the
        # existing generic failed-read behaviour.
        github = green_github()
        github.too_large.add(github.jobs_path(MAC_RUN, 1))
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.PAGES_FAILED)

    def test_run_attempt_mismatch_is_unknown_but_reports_failure(self):
        github = green_github()
        github.page(LINUX_RUN, 1, 1, [
            job(1, GATEWAY, LINUX_RUN, LINUX_ATTEMPT + 1, conclusion='failure')])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], pages.RUN_ATTEMPT_MISMATCH)
        # The failing job keeps its own attempt, not the selected run's.
        self.assertEqual(result['failing'], [{
            'kind': fetch.LINUX, 'workflow': fetch.LINUX_WORKFLOW,
            'run_id': LINUX_RUN, 'run_attempt': LINUX_ATTEMPT + 1,
            'id': 1, 'name': GATEWAY, 'conclusion': 'failure'}])
        self.assertEqual(result['runs'][0]['run_attempt'], LINUX_ATTEMPT)

    def test_run_id_mismatch_is_unknown(self):
        github = green_github()
        github.page(LINUX_RUN, 1, 1, [
            job(1, GATEWAY, LINUX_RUN + 1, LINUX_ATTEMPT, conclusion='failure')])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], pages.RUN_ID_MISMATCH)
        # The failing job keeps its own run id, not the selected run's.
        self.assertEqual(result['failing'], [{
            'kind': fetch.LINUX, 'workflow': fetch.LINUX_WORKFLOW,
            'run_id': LINUX_RUN + 1, 'run_attempt': LINUX_ATTEMPT,
            'id': 1, 'name': GATEWAY, 'conclusion': 'failure'}])
        self.assertEqual(result['runs'][0]['run_id'], LINUX_RUN)

    def test_malformed_total_page_still_reports_its_failing_job(self):
        github = green_github()
        # A usable job list on a page whose total_count is missing: the listing
        # is UNKNOWN, but the known failure must not disappear.
        github.pages[(LINUX_RUN, 1)] = {
            'jobs': [job(1, GATEWAY, LINUX_RUN, LINUX_ATTEMPT,
                         conclusion='failure')]}
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.PAGES_MALFORMED)
        self.assertEqual(result['failing'], [{
            'kind': fetch.LINUX, 'workflow': fetch.LINUX_WORKFLOW,
            'run_id': LINUX_RUN, 'run_attempt': LINUX_ATTEMPT,
            'id': 1, 'name': GATEWAY, 'conclusion': 'failure'}])

    def test_sha_mismatch_is_unknown(self):
        github = green_github()
        github.page(LINUX_RUN, 1, 1, [
            job(1, GATEWAY, LINUX_RUN, LINUX_ATTEMPT, sha=OLDER,
                conclusion='failure')])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], pages.SHA_MISMATCH)
        self.assertEqual(result['failing'], [])

    def test_missing_run_attempt_is_unknown(self):
        github = green_github()
        broken = run(LINUX_RUN, attempt=LINUX_ATTEMPT)
        del broken['run_attempt']
        github.workflow(fetch.LINUX_WORKFLOW, 'push', [broken])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.RUN_INVALID)

    def test_completed_failed_run_without_a_failing_job_is_unknown(self):
        github = green_github()
        failed = run(LINUX_RUN, attempt=LINUX_ATTEMPT)
        failed['conclusion'] = 'failure'
        github.workflow(fetch.LINUX_WORKFLOW, 'push', [failed])
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.RUN_FAILED)

    def test_main_read_failure_is_unknown(self):
        github = green_github()
        github.fail.add(fetch.MAIN)
        result = fetch.inspect_jobs(github)
        self.assertEqual(result, {'sha': None, 'state': fetch.UNKNOWN,
                                  'reason': fetch.MAIN_READ_FAILED,
                                  'runs': [], 'failing': []})

    def test_malformed_run_listing_is_unknown(self):
        github = green_github()
        github.runs[(fetch.LINUX_WORKFLOW, 'push')] = 'not a listing'
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.RUN_LIST_MALFORMED)
        self.assertEqual(result['runs'][1]['state'], fetch.GREEN)

    def test_failed_run_read_fails_the_report(self):
        github = green_github()
        github.fail.add(fetch.RUNS.format(
            workflow=fetch.LINUX_WORKFLOW, event='push',
            per_page=fetch.RUNS_PER_PAGE))
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.UNKNOWN)
        self.assertEqual(result['reason'], fetch.RUN_LIST_FAILED)
        self.assertEqual(result['runs'][1]['state'], fetch.GREEN)


if __name__ == '__main__':
    unittest.main()
