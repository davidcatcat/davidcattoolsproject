"""Tests for GitHub-hosted remote-test job lookup and runner trust."""

import unittest

from llmtool import remote_test


_SHA = '0123456789abcdef0123456789abcdef01234567'
_DIFF_SHA = 'fedcba9876543210fedcba9876543210fedcba98'


class HostedJobTests(unittest.TestCase):
    def make_run(self, *, run_id=1001, head_sha=_SHA):
        return {
            'id': run_id,
            'head_sha': head_sha,
        }

    def make_job(
        self,
        *,
        job_id=2001,
        run_id=1001,
        name='remote-test',
        status='completed',
        conclusion='success',
        labels=None,
        runner_group_name='GitHub Actions',
        check_run_id=5001,
        include_check_run_url=True,
    ):
        job = {
            'id': job_id,
            'run_id': run_id,
            'name': name,
            'status': status,
            'conclusion': conclusion,
            'labels': ['ubuntu-24.04'] if labels is None else labels,
            'runner_group_name': runner_group_name,
        }
        if include_check_run_url:
            job['check_run_url'] = (
                f'https://api.github.com/repos/davidcatcat/davidcattoolsproject/check-runs/{check_run_id}'
            )
        return job

    def make_check_run(
        self,
        *,
        check_run_id=5001,
        head_sha=_SHA,
        app_id=15368,
        status='completed',
        conclusion='success',
    ):
        return {
            'id': check_run_id,
            'head_sha': head_sha,
            'status': status,
            'conclusion': conclusion,
            'app': {
                'id': app_id,
                'slug': 'github-actions',
                'name': 'GitHub Actions',
            },
        }

    def test_one_trusted_job(self):
        run = self.make_run()
        job = self.make_job()
        check_run = self.make_check_run()

        def fake_get(path):
            if path == 'actions/runs/1001/jobs':
                return {'total_count': 1, 'jobs': [job]}
            if path == 'check-runs/5001':
                return check_run
            self.fail(f'Unexpected path: {path}')

        result = remote_test._hosted_job(run, get=fake_get)
        self.assertEqual(result, job)

    def test_no_job(self):
        run = self.make_run()

        def fake_get(path):
            self.assertEqual(path, 'actions/runs/1001/jobs')
            return {'total_count': 0, 'jobs': []}

        result = remote_test._hosted_job(run, get=fake_get)
        self.assertEqual(result, 'HOSTED_JOB_MISSING')

    def test_two_jobs(self):
        run = self.make_run()
        job1 = self.make_job(job_id=2001, check_run_id=5001)
        job2 = self.make_job(job_id=2002, check_run_id=5002)

        def fake_get(path):
            self.assertEqual(path, 'actions/runs/1001/jobs')
            return {'total_count': 2, 'jobs': [job1, job2]}

        result = remote_test._hosted_job(run, get=fake_get)
        self.assertEqual(result, 'HOSTED_JOB_MISSING')

    def test_wrong_name(self):
        run = self.make_run()
        job = self.make_job(name='other-job')

        def fake_get(path):
            self.assertEqual(path, 'actions/runs/1001/jobs')
            return {'total_count': 1, 'jobs': [job]}

        result = remote_test._hosted_job(run, get=fake_get)
        self.assertEqual(result, 'HOSTED_JOB_MISSING')

    def test_wrong_labels(self):
        run = self.make_run()
        for bad_labels in (
            [],
            ['ubuntu-22.04'],
            ['ubuntu-24.04', 'self-hosted'],
            ['ubuntu-24.04', 'extra'],
            None,
        ):
            with self.subTest(bad_labels=bad_labels):
                job = self.make_job(labels=bad_labels)

                def fake_get(path):
                    self.assertEqual(path, 'actions/runs/1001/jobs')
                    return {'total_count': 1, 'jobs': [job]}

                result = remote_test._hosted_job(run, get=fake_get)
                self.assertEqual(result, 'HOSTED_RUNNER_UNTRUSTED')

    def test_wrong_runner_group(self):
        run = self.make_run()
        for bad_group in ('Default', 'Self-hosted Runners', '', None):
            with self.subTest(bad_group=bad_group):
                job = self.make_job(runner_group_name=bad_group)

                def fake_get(path):
                    self.assertEqual(path, 'actions/runs/1001/jobs')
                    return {'total_count': 1, 'jobs': [job]}

                result = remote_test._hosted_job(run, get=fake_get)
                self.assertEqual(result, 'HOSTED_RUNNER_UNTRUSTED')

    def test_wrong_app_id(self):
        run = self.make_run()
        job = self.make_job()
        for bad_app_id in (0, 12345, None, '15368'):
            with self.subTest(bad_app_id=bad_app_id):
                check_run = self.make_check_run(app_id=bad_app_id)

                def fake_get(path):
                    if path == 'actions/runs/1001/jobs':
                        return {'total_count': 1, 'jobs': [job]}
                    if path == 'check-runs/5001':
                        return check_run
                    self.fail(f'Unexpected path: {path}')

                result = remote_test._hosted_job(run, get=fake_get)
                self.assertEqual(result, 'HOSTED_RUNNER_UNTRUSTED')

    def test_wrong_head_sha(self):
        run = self.make_run(head_sha=_SHA)
        job = self.make_job()
        check_run = self.make_check_run(head_sha=_DIFF_SHA)

        def fake_get(path):
            if path == 'actions/runs/1001/jobs':
                return {'total_count': 1, 'jobs': [job]}
            if path == 'check-runs/5001':
                return check_run
            self.fail(f'Unexpected path: {path}')

        result = remote_test._hosted_job(run, get=fake_get)
        self.assertEqual(result, 'HOSTED_RUNNER_UNTRUSTED')

    def test_queued_job(self):
        run = self.make_run()
        for status in ('queued', 'in_progress'):
            with self.subTest(status=status):
                queued_job = self.make_job(
                    status=status,
                    labels=['wrong-label'],
                    runner_group_name='wrong-group',
                    include_check_run_url=False,
                )

                def fake_get(path):
                    self.assertEqual(path, 'actions/runs/1001/jobs')
                    return {'total_count': 1, 'jobs': [queued_job]}

                result = remote_test._hosted_job(run, get=fake_get)
                self.assertEqual(result, queued_job)

    def test_read_error(self):
        run = self.make_run()

        # Exception when reading jobs
        def failing_jobs_get(path):
            raise RuntimeError('API timeout')

        self.assertEqual(
            remote_test._hosted_job(run, get=failing_jobs_get),
            'UNREADABLE',
        )

        # Malformed jobs responses
        for malformed in (
            None,
            'not a dict',
            [],
            123,
            {'jobs': 'not a list'},
            {'jobs': ['not a dict']},
        ):
            with self.subTest(malformed_jobs=malformed):
                self.assertEqual(
                    remote_test._hosted_job(run, get=lambda _: malformed),
                    'UNREADABLE',
                )

        # Exception when reading check run
        job = self.make_job()

        def failing_check_run_get(path):
            if path == 'actions/runs/1001/jobs':
                return {'total_count': 1, 'jobs': [job]}
            if path == 'check-runs/5001':
                raise RuntimeError('Check run unavailable')
            self.fail(f'Unexpected path: {path}')

        self.assertEqual(
            remote_test._hosted_job(run, get=failing_check_run_get),
            'UNREADABLE',
        )

        # Malformed check run response
        for malformed_check in (None, 'not a dict', [], 123):
            with self.subTest(malformed_check=malformed_check):
                def malformed_check_get(path):
                    if path == 'actions/runs/1001/jobs':
                        return {'total_count': 1, 'jobs': [job]}
                    if path == 'check-runs/5001':
                        return malformed_check
                    self.fail(f'Unexpected path: {path}')

                self.assertEqual(
                    remote_test._hosted_job(run, get=malformed_check_get),
                    'UNREADABLE',
                )

        # Invalid run parameter
        for bad_run in (None, 'not a dict', {}, {'id': 'not an int'}, {'id': 0}, {'id': -1}):
            with self.subTest(bad_run=bad_run):
                self.assertEqual(
                    remote_test._hosted_job(bad_run, get=lambda _: {}),
                    'UNREADABLE',
                )

    def test_missing_check_run_reference(self):
        run = self.make_run()
        job = self.make_job(include_check_run_url=False)

        def fake_get(path):
            self.assertEqual(path, 'actions/runs/1001/jobs')
            return {'total_count': 1, 'jobs': [job]}

        result = remote_test._hosted_job(run, get=fake_get)
        self.assertEqual(result, 'HOSTED_RUNNER_UNTRUSTED')


if __name__ == '__main__':
    unittest.main()
