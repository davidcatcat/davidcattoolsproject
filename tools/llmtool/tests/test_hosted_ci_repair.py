"""Explicit same-head hosted test repair after a repeated EC2 timeout."""

import copy
from dataclasses import replace
import io
import itertools
import json
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from llmtool import ci_profile, hosted_ci_repair, mac_full_due, pm_caller, review_repair
from llmtool import task_advance, task_completion, task_submission_cli
from llmtool.ci_profile import default_profile
from llmtool.cli import parser_for_cli
from llmtool.errors import LLMToolError

HEAD = 'a' * 40
TASK_ID = 'b' * 32
EXECUTOR = 'fixture-executor'
REPO = 'example/fixture'
BRANCH = 'fix/inbox'
MODULE = 'tools/llmtool/tests/test_fixture_wait.py'
PROFILE = default_profile()
WORKFLOW = f'.github/workflows/{PROFILE.linux_workflow_file}'
APP, SUITE, RUN, AGGREGATE, SHARD = 15368, 41, 42, 43, 44
SHARD_NAME = 'linux shard 3/3 (3.14)'


class HostedCiRepairTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = temp.name
        source = Path(directory) / MODULE
        source.parent.mkdir(parents=True)
        source.touch()
        self.d6 = {'result_head': HEAD, 'recorded_result_head': HEAD, 'task_id': EXECUTOR,
                   'root': directory, 'branch': BRANCH, 'state': 'CLEAN'}
        timeout = {'result': 'TIMEOUT', 'code': 'SSH_TIMEOUT', 'executor_task': EXECUTOR,
                   'sha': HEAD, 'modules': ['test_fixture_wait']}
        self.task = {'task_id': TASK_ID, 'completion': {'executor_task_id': EXECUTOR},
                     'published_pr': {'pr': 17, 'head_sha': HEAD},
                     'developer': {'read_paths': [], 'write_paths': [MODULE]},
                     'remote_test_outcomes': [timeout, dict(timeout)]}
        workflow = {'path': WORKFLOW, 'code_check': PROFILE.linux_code_check,
                    'runner_names': None, 'runner_labels': ['ubuntu-24.04']}
        self.start_patch(mock.patch.object(task_completion, '_ci_trust', return_value={
            'mode': 'github-hosted', 'github_actions_app_id': APP, 'workflows': [workflow],
        }))
        self.start_patch(mock.patch.object(task_completion, '_trusted_slug', return_value=REPO))
        self.start_patch(mock.patch.object(task_advance, '_d6_snapshot', return_value=self.d6))
        self.aggregate = self.check(AGGREGATE, PROFILE.linux_code_check)
        self.shard = self.check(SHARD, SHARD_NAME)
        self.run = {'id': RUN, 'check_suite_id': SUITE, 'head_sha': HEAD, 'head_branch': BRANCH,
                    'path': WORKFLOW, 'event': 'pull_request', 'status': 'completed',
                    'conclusion': 'failure', 'repository': {'full_name': REPO},
                    'head_repository': {'full_name': REPO}, 'pull_requests': [{'number': 17}],
                    'run_attempt': 1}
        self.jobs = [self.job(self.aggregate), self.job(self.shard)]
        self.pr = {'number': 17, 'state': 'open', 'merged': False, 'changed_files': 1,
                   'head': {'sha': HEAD, 'ref': BRANCH, 'repo': {'full_name': REPO}},
                   'base': {'repo': {'full_name': REPO}}}
        self.get = mock.Mock(side_effect=self.github)
        self.read_log = mock.Mock(return_value=(
            '2026-12-01T12:00:00.00Z ERROR: test_inbox (test_fixture_wait.Case.test_inbox)\n'
            'AttributeError: missing test fixture field\n'))

    def start_patch(self, patcher):
        self.addCleanup(patcher.stop)
        patcher.start()

    @staticmethod
    def check(check_id, name):
        return {'id': check_id, 'name': name, 'head_sha': HEAD, 'status': 'completed',
                'conclusion': 'failure', 'app': {'id': APP}, 'check_suite': {'id': SUITE},
                'details_url': f'https://github.com/{REPO}/actions/runs/{RUN}/job/{check_id}',
                'output': {}}

    @staticmethod
    def job(check):
        check_id = check['id']
        return {'id': check_id, 'name': check['name'], 'head_sha': HEAD, 'run_id': RUN,
                'run_attempt': 1, 'status': 'completed', 'conclusion': 'failure',
                'check_run_url': f'https://api.github.com/repos/{REPO}/check-runs/{check_id}',
                'labels': ['ubuntu-24.04'], 'runner_group_name': 'GitHub Actions'}

    def github(self, path):
        values = {
            'pulls/17': self.pr,
            'pulls/17/files?per_page=100': [{'filename': MODULE}],
            f'commits/{HEAD}/check-runs?per_page=100': {
                'total_count': 2, 'check_runs': [self.aggregate, self.shard]},
            f'check-runs/{AGGREGATE}': self.aggregate,
            f'check-runs/{SHARD}': self.shard,
            f'actions/runs/{RUN}': self.run,
            f'actions/runs/{RUN}/attempts/1/jobs?per_page=100': {
                'total_count': len(self.jobs), 'jobs': self.jobs},
        }
        return copy.deepcopy(values[path])

    def failure(self):
        return hosted_ci_repair.failure_for_task(
            self.task, EXECUTOR, 17, github_get=self.get, job_log_fetch=self.read_log)

    def assert_refusal(self, code):
        with self.assertRaises(LLMToolError) as caught:
            self.failure()
        self.assertEqual(caught.exception.review_repair_code, code)

    def test_uses_aggregate_for_claim_and_failed_shard_for_evidence(self):
        packet = self.failure()
        self.assertEqual(packet['check_run_id'], AGGREGATE)
        self.assertEqual(packet['job_id'], SHARD)
        self.assertEqual(packet['failing_modules'], [MODULE])
        self.assertEqual(packet['head'], HEAD)
        self.read_log.assert_called_once_with(SHARD)

    def test_requires_second_timeout_on_bound_head(self):
        self.task['remote_test_outcomes'][-1]['sha'] = 'c' * 40
        self.assert_refusal('HOSTED_CI_REPAIR_TIMEOUT_STOP_REQUIRED')
        self.get.assert_not_called()

    def test_refuses_a_newer_pending_required_check(self):
        get = self.github

        def superseded(path):
            value = get(path)
            if path.startswith('commits/'):
                value['check_runs'].append(dict(self.aggregate, id=AGGREGATE + 2,
                                                status='in_progress', conclusion=None))
                value['total_count'] += 1
            return value

        self.get.side_effect = superseded
        self.assert_refusal('HOSTED_CI_REPAIR_REQUIRED_FAILURE_MISSING')
        self.read_log.assert_not_called()

    def test_refuses_foreign_suite_before_reading_log(self):
        self.shard['check_suite']['id'] = SUITE + 1
        self.assert_refusal('HOSTED_CI_REPAIR_CHECK_INVALID')
        self.read_log.assert_not_called()

    def test_refuses_outside_scope_or_unknown_log(self):
        for log in ('ERROR: test_elsewhere (test_other.Case.test_elsewhere)\n', 'no tests\n'):
            with self.subTest(log=log):
                self.read_log.return_value = log
                self.assert_refusal('HOSTED_CI_REPAIR_OVERLAP_UNPROVEN')

    def test_refuses_when_pr_moves_during_evidence_read(self):
        def log(_job):
            self.pr['head']['sha'] = 'c' * 40
            return 'ERROR: test_inbox (test_fixture_wait.Case.test_inbox)\n'

        self.read_log.side_effect = log
        self.assert_refusal('HOSTED_CI_REPAIR_BINDING_INVALID')

    def test_cli_checks_owner_then_uses_existing_repair_entry(self):
        args = parser_for_cli().parse_args([
            'task', 'repair', TASK_ID, '--executor-task', EXECUTOR,
            '--pr', '17', '--from-hosted-linux'])
        store = mock.Mock()
        store.get.return_value = self.task
        with mock.patch.object(pm_caller, 'check_caller') as caller, \
                mock.patch.object(task_advance, '_read_ci_job_log', self.read_log), \
                mock.patch.object(review_repair, 'repair_review', return_value={'state': 'claimed'}) as repair, \
                redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(task_submission_cli.run_task_repair(args, store=store, github_get=self.get))
        caller.assert_called_once_with(store, TASK_ID, None)
        repair.assert_called_once()
        self.assertEqual(repair.call_args.kwargs['ci_failure']['check_run_id'], AGGREGATE)
        self.assertEqual(json.loads(output.getvalue()), {'state': 'claimed'})

    def test_refuses_invalid_ci_profile_without_github_read(self):
        cases = (
            ('missing', mock.patch.object(mac_full_due, 'ci_profile', SimpleNamespace())),
            ('raising', mock.patch.object(
                ci_profile, 'default_profile', side_effect=RuntimeError('profile unavailable'))),
            ('wrong_type', mock.patch.object(
                ci_profile, 'default_profile', return_value='not-a-profile')),
        )
        for kind, patcher in cases:
            with self.subTest(kind=kind), patcher:
                self.assert_refusal(review_repair.CI_REPAIR_PROFILE_INVALID)
                self.get.assert_not_called()
                self.read_log.assert_not_called()

    def test_refuses_untrusted_substituted_linux_workflow_or_check_without_github_read(self):
        cases = (
            ('workflow_file', replace(PROFILE, linux_workflow_file='other-linux-ci.yml')),
            ('check_name', replace(PROFILE, linux_code_check='other check (Linux 3.14)')),
        )
        for field, substituted in cases:
            with self.subTest(field=field), mock.patch.object(
                    ci_profile, 'default_profile', return_value=substituted):
                self.assert_refusal('HOSTED_CI_REPAIR_WORKFLOW_UNTRUSTED')
                self.get.assert_not_called()
                self.read_log.assert_not_called()

    def test_refuses_duplicate_check_name_profile_without_github_read(self):
        check_fields = ('linux_code_check', 'docs_check', 'gateway_check',
                        'ai_loop_check', 'macos_check')
        for first, second in itertools.combinations(check_fields, 2):
            dup_profile = replace(PROFILE, **{second: getattr(PROFILE, first)})
            with self.subTest(first=first, second=second), mock.patch.object(
                    ci_profile, 'default_profile', return_value=dup_profile):
                self.assert_refusal(review_repair.CI_REPAIR_PROFILE_INVALID)
                self.get.assert_not_called()
                self.read_log.assert_not_called()
