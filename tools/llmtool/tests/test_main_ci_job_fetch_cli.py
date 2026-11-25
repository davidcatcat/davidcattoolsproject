"""#2602 slice 3: ``llmtool ci main-incident jobs`` is read-only and reports failures."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import cli  # noqa: E402
from llmtool import main_ci_job_fetch as fetch  # noqa: E402
from llmtool import main_incident_cli as mic  # noqa: E402

SHA = 'a' * 40
LINUX_RUN = 101
MAC_RUN = 202
LINUX_ATTEMPT = 2
MAC_ATTEMPT = 1
GATEWAY = 'gateway tests (Linux 3.14)'
LINUX_JOB = 'check (Linux 3.14)'
MAC_JOB = 'check (macOS 3.13)'
MAC_SHARDS = ('shards (1/3)', 'shards (2/3)', 'shards (3/3)')


def job(job_id, name, run_id, attempt, conclusion='success'):
    return {'id': job_id, 'name': name, 'run_id': run_id,
            'run_attempt': attempt, 'head_sha': SHA,
            'status': 'completed', 'conclusion': conclusion}


class FakeGitHub:
    """A deterministic read-only fake; no network and no credential-shaped data."""

    def __init__(self):
        self.pages = {}
        self.fail = set()
        self.calls = []

    def page(self, run_id, total, jobs):
        self.pages[run_id] = {'total_count': total, 'jobs': list(jobs)}

    def jobs_path(self, run_id):
        return fetch.JOBS.format(run_id=run_id, per_page=fetch.JOBS_PER_PAGE, page=1)

    def __call__(self, path):
        self.calls.append(path)
        if path in self.fail:
            raise OSError('api down')
        if path == fetch.MAIN:
            return {'commit': {'sha': SHA}}
        if path == fetch.RUNS.format(workflow=fetch.LINUX_WORKFLOW, event='push',
                                     per_page=fetch.RUNS_PER_PAGE):
            return {'workflow_runs': [{'id': LINUX_RUN, 'head_sha': SHA,
                                       'run_attempt': LINUX_ATTEMPT,
                                       'status': 'completed', 'conclusion': 'success'}]}
        if path == fetch.RUNS.format(workflow=fetch.MAC_WORKFLOW,
                                     event='workflow_dispatch',
                                     per_page=fetch.RUNS_PER_PAGE):
            return {'workflow_runs': [{'id': MAC_RUN, 'head_sha': SHA,
                                       'run_attempt': MAC_ATTEMPT,
                                       'status': 'completed', 'conclusion': 'success'}]}
        for run_id, data in self.pages.items():
            if path == self.jobs_path(run_id):
                return data
        raise KeyError(path)


def mac_full_jobs():
    jobs = [job(2 + index, name, MAC_RUN, MAC_ATTEMPT)
            for index, name in enumerate(MAC_SHARDS)]
    return jobs + [job(5, MAC_JOB, MAC_RUN, MAC_ATTEMPT)]


def mac_skip_jobs(run_id=MAC_RUN, attempt=MAC_ATTEMPT):
    return [job(20, 'select', run_id, attempt)] + [
        job(21 + index, name, run_id, attempt, conclusion='skipped')
        for index, name in enumerate(MAC_SHARDS)] + [
        job(24, MAC_JOB, run_id, attempt)]


def green_github():
    github = FakeGitHub()
    github.page(LINUX_RUN, 1, [job(1, LINUX_JOB, LINUX_RUN, LINUX_ATTEMPT)])
    github.page(MAC_RUN, len(mac_full_jobs()), mac_full_jobs())
    return github


class MainCiJobFetchCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'state' / 'tasks'

    def run_cli(self, github):
        args = cli.parser_for_cli().parse_args(['ci', 'main-incident', 'jobs'])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = mic.run_main_incident(args, github_get=github, root=self.root)
        return status, json.loads(out.getvalue()), err.getvalue()

    def test_jobs_is_a_main_incident_action(self):
        args = cli.parser_for_cli().parse_args(['ci', 'main-incident', 'jobs'])
        self.assertEqual((args.command, args.action, args.incident_action),
                         ('ci', 'main-incident', 'jobs'))
        self.assertFalse(hasattr(args, 'incident'))

    def test_green_report_is_machine_readable(self):
        status, payload, err = self.run_cli(green_github())
        self.assertEqual(status, 0)
        self.assertEqual(err, '')
        self.assertEqual(payload['sha'], SHA)
        self.assertEqual((payload['state'], payload['reason']), (fetch.GREEN, None))
        self.assertEqual(payload['failing'], [])
        self.assertEqual(
            [(run['kind'], run['workflow'], run['run_id'], run['run_attempt'])
             for run in payload['runs']],
            [(fetch.LINUX, fetch.LINUX_WORKFLOW, LINUX_RUN, LINUX_ATTEMPT),
             (fetch.FULL_MAC, fetch.MAC_WORKFLOW, MAC_RUN, MAC_ATTEMPT)])

    def test_skip_only_mac_dispatch_is_unknown_not_green(self):
        github = green_github()
        github.page(MAC_RUN, 5, mac_skip_jobs())
        status, payload, err = self.run_cli(github)
        self.assertEqual((status, payload['state'], payload['reason']),
                         (2, fetch.UNKNOWN, fetch.FULL_EVIDENCE_MISSING))
        self.assertEqual(err, f'llmtool: {fetch.FULL_EVIDENCE_MISSING}\n')
        self.assertEqual(payload['runs'][1]['run_id'], MAC_RUN)

    def test_red_report_lists_non_required_job_with_run_identity(self):
        github = green_github()
        github.page(LINUX_RUN, 2, [
            job(1, LINUX_JOB, LINUX_RUN, LINUX_ATTEMPT),
            job(2, GATEWAY, LINUX_RUN, LINUX_ATTEMPT, conclusion='failure')])
        status, payload, err = self.run_cli(github)
        self.assertEqual((status, payload['state'], payload['reason']),
                         (1, fetch.RED, None))
        self.assertEqual(err, '')
        self.assertNotIn('refused', payload)
        self.assertNotIn('incident_id', payload)
        self.assertEqual(payload['failing'], [{
            'kind': fetch.LINUX, 'workflow': fetch.LINUX_WORKFLOW,
            'run_id': LINUX_RUN, 'run_attempt': LINUX_ATTEMPT,
            'id': 2, 'name': GATEWAY, 'conclusion': 'failure'}])

    def test_unknown_report_exits_2_with_the_reason(self):
        github = green_github()
        github.fail.add(github.jobs_path(MAC_RUN))
        status, payload, err = self.run_cli(github)
        self.assertEqual((status, payload['state'], payload['reason']),
                         (2, fetch.UNKNOWN, fetch.PAGES_FAILED))
        self.assertEqual(err, f'llmtool: {fetch.PAGES_FAILED}\n')
        self.assertEqual(payload['runs'][1]['reason'], fetch.PAGES_FAILED)

    def test_jobs_never_touches_the_store_or_the_scan_path(self):
        github = green_github()
        with mock.patch.object(mic, '_scan',
                               side_effect=AssertionError('scan path used')), \
                mock.patch.object(mic, '_caller',
                                  side_effect=AssertionError('caller resolved')), \
                mock.patch.object(mic.store, 'create',
                                  side_effect=AssertionError('store created')), \
                mock.patch.object(mic.store, 'append_observation',
                                  side_effect=AssertionError('store updated')):
            status, payload, err = self.run_cli(github)
        self.assertEqual((status, payload['state'], err), (0, fetch.GREEN, ''))


if __name__ == '__main__':
    unittest.main()
