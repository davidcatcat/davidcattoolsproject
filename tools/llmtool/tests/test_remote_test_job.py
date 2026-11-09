"""Tests for GitHub-hosted remote-test job selection and runner trust."""

import unittest

from llmtool import remote_test


_SHA = '0123456789abcdef0123456789abcdef01234567'
_RUN_ID = 8675309
_JOB_ID = 42
_CHECK_ID = 5551234
_CHECK_URL = (
    f'https://api.github.com/repos/example-owner/example-repo/check-runs/{_CHECK_ID}'
)
_JOBS_PATH = f'actions/runs/{_RUN_ID}/jobs?per_page=100'
_CHECK_PATH = f'check-runs/{_CHECK_ID}'
_CHECKS_PATH = f'commits/{_SHA}/check-runs?per_page=100'


def _run(**overrides):
    value = {'id': _RUN_ID, 'head_sha': _SHA}
    value.update(overrides)
    return value


def _job(**overrides):
    value = {
        'id': _JOB_ID,
        'name': 'remote-test',
        'status': 'completed',
        'conclusion': 'success',
        'labels': ['ubuntu-24.04'],
        'runner_group_name': 'GitHub Actions',
        'check_run_url': _CHECK_URL,
    }
    value.update(overrides)
    return value


def _check(**overrides):
    value = {
        'id': _CHECK_ID,
        'app': {'id': 15368},
        'head_sha': _SHA,
    }
    value.update(overrides)
    return value


def _getter(jobs, check=None, *, jobs_error=False, check_error=False):
    def get(path):
        if path == _JOBS_PATH:
            if jobs_error:
                raise RuntimeError('jobs unreadable')
            return {'total_count': len(jobs), 'jobs': jobs}
        if check_error:
            raise RuntimeError('check unreadable')
        if path == _CHECK_PATH:
            return check
        if path == _CHECKS_PATH:
            return {'total_count': 1, 'check_runs': [check]}
        raise RuntimeError('unexpected path')

    return get


class HostedJobTests(unittest.TestCase):
    def test_trusted_job_is_returned(self):
        job = _job()
        found = remote_test._hosted_job(
            _run(), get=_getter([job], _check()),
        )
        self.assertEqual(found, job)

    def test_no_jobs(self):
        found = remote_test._hosted_job(
            _run(), get=_getter([]),
        )
        self.assertEqual(found, 'HOSTED_JOB_MISSING')

    def test_two_matching_jobs(self):
        jobs = [_job(), _job(id=43)]
        found = remote_test._hosted_job(
            _run(), get=_getter(jobs, _check()),
        )
        self.assertEqual(found, 'HOSTED_JOB_MISSING')

    def test_two_jobs_with_one_match(self):
        job = _job()
        jobs = [_job(id=43, name='other-job'), job]
        found = remote_test._hosted_job(
            _run(), get=_getter(jobs, _check()),
        )
        self.assertEqual(found, job)

    def test_wrong_job_name(self):
        jobs = [_job(name='other-job')]
        found = remote_test._hosted_job(
            _run(), get=_getter(jobs, _check()),
        )
        self.assertEqual(found, 'HOSTED_JOB_MISSING')

    def test_wrong_labels(self):
        jobs = [_job(labels=['ubuntu-latest'])]
        found = remote_test._hosted_job(
            _run(), get=_getter(jobs, _check()),
        )
        self.assertEqual(found, 'HOSTED_RUNNER_UNTRUSTED')

    def test_wrong_runner_group(self):
        jobs = [_job(runner_group_name='self-hosted')]
        found = remote_test._hosted_job(
            _run(), get=_getter(jobs, _check()),
        )
        self.assertEqual(found, 'HOSTED_RUNNER_UNTRUSTED')

    def test_wrong_app_id(self):
        jobs = [_job()]
        found = remote_test._hosted_job(
            _run(), get=_getter(jobs, _check(app={'id': 999})),
        )
        self.assertEqual(found, 'HOSTED_RUNNER_UNTRUSTED')

    def test_wrong_check_head_sha(self):
        jobs = [_job()]
        found = remote_test._hosted_job(
            _run(), get=_getter(jobs, _check(head_sha='f' * 40)),
        )
        self.assertEqual(found, 'HOSTED_RUNNER_UNTRUSTED')

    def test_queued_and_in_progress_jobs_bypass_trust_checks(self):
        for status in ('queued', 'in_progress'):
            with self.subTest(status=status):
                job = _job(
                    status=status,
                    labels=['wrong-label'],
                    runner_group_name='wrong-group',
                    check_run_url=None,
                )
                found = remote_test._hosted_job(
                    _run(), get=_getter([job]),
                )
                self.assertEqual(found, job)

    def test_jobs_read_error(self):
        found = remote_test._hosted_job(
            _run(), get=_getter([], jobs_error=True),
        )
        self.assertEqual(found, 'UNREADABLE')

    def test_check_read_error(self):
        found = remote_test._hosted_job(
            _run(), get=_getter([_job()], _check(), check_error=True),
        )
        self.assertEqual(found, 'UNREADABLE')


if __name__ == '__main__':
    unittest.main()
