"""Unit tests for Mac CI capacity cancel routing (#1786)."""

import unittest

from llmtool import task_advance

HEAD = 'a' * 40
WORKFLOW_RUN_ID = 101
JOB_ID = 202
NUMBER = 17
DETAILS_URL = f'https://example.test/actions/runs/{WORKFLOW_RUN_ID}/job/{JOB_ID}'
MAC_CHECK = 'check (macOS 3.13)'


class DummyStore:
    def __init__(self, record):
        self.record = record

    def get(self, _task_id):
        return self.record

    def task_ids(self):
        return [self.record['task_id']]


def _make_run(run_id=1, name=MAC_CHECK, conclusion='failure', details_url=DETAILS_URL, head=HEAD):
    run = {
        'id': run_id,
        'name': name,
        'conclusion': conclusion,
        'head_sha': head,
    }
    if details_url is not None:
        run['details_url'] = details_url
    return run


def _make_job(name, conclusion='success', steps=None, head=HEAD, job_id=JOB_ID):
    job = {
        'name': name,
        'conclusion': conclusion,
        'head_sha': head,
    }
    if job_id is not None:
        job['id'] = job_id
    if steps is not None:
        job['steps'] = steps
    return job


def _make_fake_github_get(jobs_payload, files=None, workflow_run=None, number=NUMBER):
    if files is None:
        files = [{'filename': 'tools/llmtool/llmtool/task_advance.py'}]
    if workflow_run is None:
        workflow_run = {'head_sha': HEAD, 'path': '.github/workflows/llmtool-mac-ci.yml'}

    def fake_github_get(path):
        if path == f'pulls/{number}/files?per_page=100':
            if isinstance(files, Exception):
                raise files
            return files
        if path == f'actions/runs/{WORKFLOW_RUN_ID}':
            if isinstance(workflow_run, Exception):
                raise workflow_run
            return workflow_run
        if path == f'actions/runs/{WORKFLOW_RUN_ID}/jobs?per_page=100':
            if isinstance(jobs_payload, Exception):
                raise jobs_payload
            return jobs_payload
        raise AssertionError(f'Unexpected github_get path: {path}')

    return fake_github_get


def _call_route(run, github_get, repair_calls, number=NUMBER):
    record = {
        'task_id': '1' * 32,
        'run_id': '2' * 32,
        'developer': {'provider_profile': 'custom'},
        'review_repair_claims': [],
    }
    d6 = {'result_head': HEAD, 'root': '/dummy/worktree'}
    related = {}
    return task_advance._route_ci_failure(
        store=DummyStore(record),
        record=record,
        task_id=record['task_id'],
        executor_task='exec-1',
        number=number,
        d6=d6,
        related=related,
        run=run,
        fail_code='CI_MAC_FAILED',
        github_get=github_get,
        repair_review_call=lambda **kw: repair_calls.append(kw) or {'repair': 'sent'},
    )


class TestTaskAdvanceMacCapacity(unittest.TestCase):
    def test_capacity_select_cancelled_shards_skipped_step_shard_results(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Source revision', 'conclusion': 'success'},
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='cancelled'),
            _make_job('shards (1/3)', conclusion='skipped'),
            _make_job('shards (2/3)', conclusion='skipped'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        # Direct call to _mac_capacity_cancel
        direct_result = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNotNone(direct_result)
        step, codes, evidence = direct_result
        self.assertEqual(step, 'WAITING_ENV')
        self.assertEqual(codes, ['CI_ENV_MAC_CAPACITY_CANCELLED'])

        # Call via _route_ci_failure with repair_review_call
        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(repair_calls, [])
        self.assertEqual(route_result, direct_result)

    def test_capacity_select_startup_failure(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='startup_failure'),
            _make_job('shards (1/3)', conclusion='skipped'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(repair_calls, [])
        self.assertIsNotNone(route_result)
        step, codes, _evidence = route_result
        self.assertEqual(step, 'WAITING_ENV')
        self.assertEqual(codes, ['CI_ENV_MAC_CAPACITY_CANCELLED'])

    def test_capacity_select_success_one_shard_cancelled_others_skipped(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='success'),
            _make_job('shards (1/3)', conclusion='cancelled'),
            _make_job('shards (2/3)', conclusion='skipped'),
            _make_job('shards (3/3)', conclusion='skipped'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(repair_calls, [])
        self.assertIsNotNone(route_result)
        step, codes, _evidence = route_result
        self.assertEqual(step, 'WAITING_ENV')
        self.assertEqual(codes, ['CI_ENV_MAC_CAPACITY_CANCELLED'])

    def test_none_subset_mode_select_success_all_shards_skipped_failed_at_test_step(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Run tests', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='success'),
            _make_job('shards (1/3)', conclusion='skipped'),
            _make_job('shards (2/3)', conclusion='skipped'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_subset_mode_select_success_all_shards_skipped_no_cancelled_shard(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='success'),
            _make_job('shards (1/3)', conclusion='skipped'),
            _make_job('shards (2/3)', conclusion='skipped'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_mixed_shard_failure_plus_one_cancelled(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='success'),
            _make_job('shards (1/3)', conclusion='cancelled'),
            _make_job('shards (2/3)', conclusion='failure'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_check_failed_step_other_than_shard_results(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Source revision', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='cancelled'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_head_sha_mismatch(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='cancelled', head='b' * 40),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_unknown_conclusion(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='unexpected_state'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_total_count_mismatch(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='cancelled'),
            mac_job,
        ]
        payload = {'total_count': 99, 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_jobs_read_error(self):
        run = _make_run()
        fake_github_get = _make_fake_github_get(jobs_payload=RuntimeError('read failed'))

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_details_url_missing(self):
        run = _make_run(details_url=None)
        fake_github_get = lambda _path: {}

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_non_mac_check_name(self):
        run = _make_run(name='check (Linux 3.14)')
        fake_github_get = lambda _path: {}

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_evidence_contract_exact_five_keys_and_no_url_or_log(self):
        run = _make_run()
        long_name = 's' * 120
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='cancelled'),
            _make_job(long_name, conclusion='skipped'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        result = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNotNone(result)
        _step, _codes, evidence = result

        # Exactly the five keys
        expected_keys = {'check', 'check_run_id', 'head', 'workflow_run_id', 'jobs'}
        self.assertEqual(set(evidence.keys()), expected_keys)
        self.assertEqual(len(evidence), 5)

        # Check values
        self.assertEqual(evidence['check'], MAC_CHECK)
        self.assertEqual(evidence['check_run_id'], run['id'])
        self.assertEqual(evidence['head'], HEAD)
        self.assertEqual(evidence['workflow_run_id'], WORKFLOW_RUN_ID)

        # Job names cut to 100 characters
        self.assertIn('s' * 100, evidence['jobs'])
        self.assertNotIn(long_name, evidence['jobs'])

        # No URL or log text in evidence
        for key, value in evidence.items():
            if isinstance(value, str):
                self.assertNotIn('://', value)
                self.assertNotIn('error', value.lower())
            elif isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    self.assertNotIn('://', str(sub_key))
                    self.assertNotIn('://', str(sub_value))

    def test_none_pr_changes_mac_workflow(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='cancelled'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        files = [{'filename': '.github/workflows/llmtool-mac-ci.yml'}]
        fake_github_get = _make_fake_github_get(payload, files=files)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_pr_renames_from_github_directory(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='cancelled'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        files = [{'filename': 'tools/moved.py', 'previous_filename': '.github/workflows/old.yml'}]
        fake_github_get = _make_fake_github_get(payload, files=files)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_pr_files_unreadable(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='cancelled'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload, files=RuntimeError('files unreadable'))

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_workflow_run_path_differs(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='cancelled'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        workflow_run = {'head_sha': HEAD, 'path': '.github/workflows/other.yml'}
        fake_github_get = _make_fake_github_get(payload, workflow_run=workflow_run)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_workflow_run_head_sha_differs(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='cancelled'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        workflow_run = {'head_sha': 'b' * 40, 'path': '.github/workflows/llmtool-mac-ci.yml'}
        fake_github_get = _make_fake_github_get(payload, workflow_run=workflow_run)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_details_url_job_id_differs_from_check_job_id(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ], job_id=999)
        jobs = [
            _make_job('select', conclusion='cancelled'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_timed_out_select_with_skipped_shards(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='timed_out'),
            _make_job('shards (1/2)', conclusion='skipped'),
            _make_job('shards (2/2)', conclusion='skipped'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_select_success_all_shards_timed_out(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='success'),
            _make_job('shards (1/2)', conclusion='timed_out'),
            _make_job('shards (2/2)', conclusion='timed_out'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_select_success_one_cancelled_one_timed_out_shard(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='success'),
            _make_job('shards (1/2)', conclusion='cancelled'),
            _make_job('shards (2/2)', conclusion='timed_out'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_select_cancelled_with_timed_out_shard(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='cancelled'),
            _make_job('shards (1/2)', conclusion='timed_out'),
            _make_job('shards (2/2)', conclusion='skipped'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')

    def test_none_select_startup_failure_with_timed_out_shard(self):
        run = _make_run()
        mac_job = _make_job(MAC_CHECK, conclusion='failure', steps=[
            {'name': 'Shard results', 'conclusion': 'failure'},
        ])
        jobs = [
            _make_job('select', conclusion='startup_failure'),
            _make_job('shards (1/2)', conclusion='timed_out'),
            _make_job('shards (2/2)', conclusion='skipped'),
            mac_job,
        ]
        payload = {'total_count': len(jobs), 'jobs': jobs}
        fake_github_get = _make_fake_github_get(payload)

        direct = task_advance._mac_capacity_cancel(run, HEAD, fake_github_get, NUMBER)
        self.assertIsNone(direct)

        repair_calls = []
        route_result = _call_route(run, fake_github_get, repair_calls)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(route_result[0], 'CI_FAILED')


if __name__ == '__main__':
    unittest.main()
