"""Tests for Mac CI capacity cancel classification in task advance."""

import unittest

from llmtool import task_advance


HEAD = '0' * 40
MAC_CHECK = 'check (macOS 3.13)'
WORKFLOW_RUN_ID = 12345
DETAILS_URL = f'https://github.com/example/repo/actions/runs/{WORKFLOW_RUN_ID}/job/67890'


def make_check_run(
    run_id=5,
    *,
    name=MAC_CHECK,
    conclusion='failure',
    details_url=DETAILS_URL,
):
    run = {'id': run_id, 'name': name, 'conclusion': conclusion}
    if details_url is not None:
        run['details_url'] = details_url
    return run


def make_job(name, conclusion, *, head=HEAD, steps=None):
    job = {
        'id': 100,
        'name': name,
        'conclusion': conclusion,
        'head_sha': head,
    }
    if steps is not None:
        job['steps'] = steps
    return job


def make_mac_steps(failed_step_name='Shard results'):
    return [
        {'name': 'Source revision', 'conclusion': 'success'},
        {'name': failed_step_name, 'conclusion': 'failure'},
    ]


class DummyStore:
    def get(self, _task_id):
        return None

    def task_ids(self):
        return []


def call_route_ci_failure(run, github_get, repair_calls, *, fail_code='MAC_CI_FAILED'):
    d6 = {'root': '/repo/task', 'result_head': HEAD, 'task_id': 'executor-1'}
    record = {'task_id': '1' * 32, 'developer': {}}
    related = {}
    return task_advance._route_ci_failure(
        store=DummyStore(),
        record=record,
        task_id='1' * 32,
        executor_task='executor-1',
        number=17,
        d6=d6,
        related=related,
        run=run,
        fail_code=fail_code,
        github_get=github_get,
        repair_review_call=lambda **kw: repair_calls.append(kw) or {'repair': 'sent'},
    )


class TestTaskAdvanceMacCapacity(unittest.TestCase):
    """Test Mac CI capacity cancellation detection and routing."""

    def test_capacity_select_cancelled_shards_skipped(self):
        run = make_check_run()
        jobs = [
            make_job('select', 'cancelled'),
            make_job('shards (1/3)', 'skipped'),
            make_job('shards (2/3)', 'skipped'),
            make_job('shards (3/3)', 'skipped'),
            make_job(MAC_CHECK, 'failure', steps=make_mac_steps('Shard results')),
        ]
        gh_get = lambda path: {'total_count': len(jobs), 'jobs': jobs}

        result = task_advance._mac_capacity_cancel(run, HEAD, gh_get)
        self.assertIsNotNone(result)
        step, codes, evidence = result
        self.assertEqual(step, 'WAITING_ENV')
        self.assertEqual(codes, ['CI_ENV_MAC_CAPACITY_CANCELLED'])

        repaired = []
        routed = call_route_ci_failure(run, gh_get, repaired)
        self.assertEqual(routed, result)
        self.assertEqual(repaired, [])

    def test_capacity_select_startup_failure(self):
        run = make_check_run()
        jobs = [
            make_job('select', 'startup_failure'),
            make_job('shards (1/3)', 'skipped'),
            make_job(MAC_CHECK, 'failure', steps=make_mac_steps('Shard results')),
        ]
        gh_get = lambda path: {'total_count': len(jobs), 'jobs': jobs}

        repaired = []
        routed = call_route_ci_failure(run, gh_get, repaired)
        self.assertIsNotNone(routed)
        self.assertEqual(routed[0], 'WAITING_ENV')
        self.assertEqual(routed[1], ['CI_ENV_MAC_CAPACITY_CANCELLED'])
        self.assertEqual(repaired, [])

    def test_capacity_select_success_one_shard_cancelled(self):
        run = make_check_run()
        jobs = [
            make_job('select', 'success'),
            make_job('shards (1/3)', 'cancelled'),
            make_job('shards (2/3)', 'skipped'),
            make_job('shards (3/3)', 'skipped'),
            make_job(MAC_CHECK, 'failure', steps=make_mac_steps('Shard results')),
        ]
        gh_get = lambda path: {'total_count': len(jobs), 'jobs': jobs}

        repaired = []
        routed = call_route_ci_failure(run, gh_get, repaired)
        self.assertIsNotNone(routed)
        self.assertEqual(routed[0], 'WAITING_ENV')
        self.assertEqual(routed[1], ['CI_ENV_MAC_CAPACITY_CANCELLED'])
        self.assertEqual(repaired, [])

    def test_none_subset_mode(self):
        run = make_check_run()
        jobs = [
            make_job('select', 'success'),
            make_job('shards (1/3)', 'skipped'),
            make_job('shards (2/3)', 'skipped'),
            make_job(MAC_CHECK, 'failure', steps=[
                {'name': 'Source revision', 'conclusion': 'success'},
                {'name': 'Shard results', 'conclusion': 'success'},
                {'name': 'Run tests', 'conclusion': 'failure'},
            ]),
        ]
        gh_get = lambda path: {'total_count': len(jobs), 'jobs': jobs}

        self.assertIsNone(task_advance._mac_capacity_cancel(run, HEAD, gh_get))
        repaired = []
        routed = call_route_ci_failure(run, gh_get, repaired)
        self.assertEqual(routed[0], 'CI_FAILED')
        self.assertEqual(len(repaired), 1)

    def test_none_mixed_shard_failure_plus_cancelled(self):
        run = make_check_run()
        jobs = [
            make_job('select', 'success'),
            make_job('shards (1/3)', 'failure'),
            make_job('shards (2/3)', 'cancelled'),
            make_job(MAC_CHECK, 'failure', steps=make_mac_steps('Shard results')),
        ]
        gh_get = lambda path: {'total_count': len(jobs), 'jobs': jobs}

        self.assertIsNone(task_advance._mac_capacity_cancel(run, HEAD, gh_get))
        repaired = []
        routed = call_route_ci_failure(run, gh_get, repaired)
        self.assertEqual(routed[0], 'CI_FAILED')
        self.assertEqual(len(repaired), 1)

    def test_none_check_failed_step_not_shard_results(self):
        run = make_check_run()
        jobs = [
            make_job('select', 'cancelled'),
            make_job('shards (1/3)', 'skipped'),
            make_job(MAC_CHECK, 'failure', steps=[
                {'name': 'Source revision', 'conclusion': 'failure'},
                {'name': 'Shard results', 'conclusion': 'success'},
            ]),
        ]
        gh_get = lambda path: {'total_count': len(jobs), 'jobs': jobs}

        self.assertIsNone(task_advance._mac_capacity_cancel(run, HEAD, gh_get))
        repaired = []
        routed = call_route_ci_failure(run, gh_get, repaired)
        self.assertEqual(routed[0], 'CI_FAILED')
        self.assertEqual(len(repaired), 1)

    def test_none_head_sha_mismatch(self):
        run = make_check_run()
        other_head = '1' * 40
        jobs = [
            make_job('select', 'cancelled', head=other_head),
            make_job('shards (1/3)', 'skipped'),
            make_job(MAC_CHECK, 'failure', steps=make_mac_steps('Shard results')),
        ]
        gh_get = lambda path: {'total_count': len(jobs), 'jobs': jobs}

        self.assertIsNone(task_advance._mac_capacity_cancel(run, HEAD, gh_get))

    def test_none_unknown_conclusion(self):
        run = make_check_run()
        jobs = [
            make_job('select', 'unexpected_status'),
            make_job('shards (1/3)', 'skipped'),
            make_job(MAC_CHECK, 'failure', steps=make_mac_steps('Shard results')),
        ]
        gh_get = lambda path: {'total_count': len(jobs), 'jobs': jobs}

        self.assertIsNone(task_advance._mac_capacity_cancel(run, HEAD, gh_get))

    def test_none_total_count_mismatch(self):
        run = make_check_run()
        jobs = [
            make_job('select', 'cancelled'),
            make_job('shards (1/3)', 'skipped'),
            make_job(MAC_CHECK, 'failure', steps=make_mac_steps('Shard results')),
        ]
        gh_get = lambda path: {'total_count': 99, 'jobs': jobs}
        self.assertIsNone(task_advance._mac_capacity_cancel(run, HEAD, gh_get))

        gh_get_over = lambda path: {'total_count': 101, 'jobs': jobs}
        self.assertIsNone(task_advance._mac_capacity_cancel(run, HEAD, gh_get_over))

    def test_none_jobs_read_error(self):
        run = make_check_run()
        def gh_get(_path):
            raise RuntimeError('github api error')

        self.assertIsNone(task_advance._mac_capacity_cancel(run, HEAD, gh_get))
        repaired = []
        routed = call_route_ci_failure(run, gh_get, repaired)
        self.assertEqual(routed[0], 'CI_FAILED')
        self.assertEqual(len(repaired), 1)

    def test_none_details_url_missing(self):
        run = make_check_run(details_url=None)
        gh_get = lambda path: {'total_count': 0, 'jobs': []}
        self.assertIsNone(task_advance._mac_capacity_cancel(run, HEAD, gh_get))

        run_bad = make_check_run(details_url='https://example.com/invalid')
        self.assertIsNone(task_advance._mac_capacity_cancel(run_bad, HEAD, gh_get))

    def test_none_non_mac_check_name(self):
        run = make_check_run(name='check (Linux 3.14)')
        gh_get = lambda path: {'total_count': 0, 'jobs': []}

        repaired = []
        routed = call_route_ci_failure(run, gh_get, repaired, fail_code='LINUX_CI_FAILED')
        self.assertEqual(routed[0], 'CI_FAILED')
        self.assertEqual(len(repaired), 1)

    def test_evidence_dict_structure(self):
        run = make_check_run(run_id=42)
        long_job_name = 's' * 150
        jobs = [
            make_job('select', 'cancelled'),
            make_job(long_job_name, 'skipped'),
            make_job(MAC_CHECK, 'failure', steps=make_mac_steps('Shard results')),
        ]
        gh_get = lambda path: {'total_count': len(jobs), 'jobs': jobs}

        result = task_advance._mac_capacity_cancel(run, HEAD, gh_get)
        self.assertIsNotNone(result)
        _, _, evidence = result

        expected_keys = {'check', 'check_run_id', 'head', 'workflow_run_id', 'jobs'}
        self.assertEqual(set(evidence.keys()), expected_keys)
        self.assertEqual(len(evidence), 5)

        self.assertEqual(evidence['check'], MAC_CHECK)
        self.assertEqual(evidence['check_run_id'], 42)
        self.assertEqual(evidence['head'], HEAD)
        self.assertEqual(evidence['workflow_run_id'], WORKFLOW_RUN_ID)
        self.assertIsInstance(evidence['jobs'], dict)

        for _k, v in evidence.items():
            if isinstance(v, str):
                self.assertNotIn('http:', v)
                self.assertNotIn('https:', v)
                self.assertNotIn('://', v)
                self.assertNotIn('\n', v)
        for job_name, conclusion in evidence['jobs'].items():
            self.assertNotIn('http:', job_name)
            self.assertNotIn('https:', job_name)
            self.assertNotIn('://', job_name)
            self.assertLessEqual(len(job_name), 100)
            self.assertNotIn('\n', job_name)
            self.assertNotIn('\n', conclusion)

        self.assertIn('s' * 100, evidence['jobs'])
        self.assertNotIn(long_job_name, evidence['jobs'])


if __name__ == '__main__':
    unittest.main()
