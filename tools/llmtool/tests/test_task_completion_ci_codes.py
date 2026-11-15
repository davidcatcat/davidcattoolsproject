import unittest
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool import task_completion

SLUG = 'owner/repo'
SHA = 'a' * 40
WP = '.github/workflows/ci.yml'
WORKFLOW = {'id': 7, 'path': WP, 'name': 'CI', 'state': 'active'}

GOOD_RUN = {
    'workflow_id': 7,
    'path': WP,
    'event': 'pull_request',
    'head_sha': SHA,
    'pull_requests': [{'number': 5, 'head': {'sha': SHA}}],
    'id': 11,
    'run_number': 1,
    'run_attempt': 1,
    'status': 'completed',
    'conclusion': 'success',
}

GOOD_JOB = {
    'name': 'check',
    'check_run_url': 'https://api.github.com/repos/owner/repo/check-runs/99',
    'run_id': 11,
    'id': 21,
    'status': 'completed',
    'conclusion': 'success',
}

GOOD_CHECK = {
    'id': 99,
    'head_sha': SHA,
    'name': 'check',
    'status': 'completed',
    'conclusion': 'success',
    'app': {
        'id': task_completion._GITHUB_ACTIONS_APP_ID,
        'slug': 'github-actions',
        'name': 'GitHub Actions',
    },
}


def call(runs, checks):
    def fake_github_get(path):
        if path.startswith('actions/workflows/ci.yml'):
            return WORKFLOW
        if '/runs?' in path:
            return runs
        if '/jobs' in path:
            return {'total_count': 1, 'jobs': [GOOD_JOB]}
        if 'check-runs' in path:
            return checks
        raise AssertionError(f'Unexpected github_get path: {path}')

    with patch.object(task_completion, '_trusted_slug', return_value=SLUG), \
            patch.object(task_completion, '_trusted_ci_runner', return_value=True), \
            patch.object(task_completion, '_github_get', side_effect=fake_github_get):
        return task_completion._collect_one_ci_workflow(
            pr_number=5,
            result_sha=SHA,
            workflow_path=WP,
            workflow_name='CI',
            check_name='check',
            runner_names=('r',),
        )


class TestTaskCompletionCiCodes(unittest.TestCase):
    def test_control_passing(self):
        good_runs = {'total_count': 1, 'workflow_runs': [GOOD_RUN]}
        good_checks = {'total_count': 1, 'check_runs': [GOOD_CHECK]}
        result = call(good_runs, good_checks)
        self.assertEqual(result['successful_checks'], ['check'])
        self.assertEqual(result['check_ids'], {'check': 99})
        self.assertEqual(result['check_outcomes'], {'check': 'SUCCESS'})

    def test_ci_workflow_runs_invalid_total_count_mismatch(self):
        runs = {'total_count': 2, 'workflow_runs': [GOOD_RUN]}
        good_checks = {'total_count': 1, 'check_runs': [GOOD_CHECK]}
        with self.assertRaises(LLMToolError) as ctx:
            call(runs, good_checks)
        self.assertEqual(ctx.exception.completion_code, 'CI_WORKFLOW_RUNS_INVALID')

    def test_ci_workflow_runs_invalid_entry_not_dict(self):
        runs = {'total_count': 1, 'workflow_runs': ['x']}
        good_checks = {'total_count': 1, 'check_runs': [GOOD_CHECK]}
        with self.assertRaises(LLMToolError) as ctx:
            call(runs, good_checks)
        self.assertEqual(ctx.exception.completion_code, 'CI_WORKFLOW_RUNS_INVALID')

    def test_ci_workflow_runs_invalid_run_id_zero(self):
        bad_run = dict(GOOD_RUN, id=0)
        runs = {'total_count': 1, 'workflow_runs': [bad_run]}
        good_checks = {'total_count': 1, 'check_runs': [GOOD_CHECK]}
        with self.assertRaises(LLMToolError) as ctx:
            call(runs, good_checks)
        self.assertEqual(ctx.exception.completion_code, 'CI_WORKFLOW_RUNS_INVALID')

    def test_ci_workflow_runs_invalid_runs_not_dict(self):
        runs = None
        good_checks = {'total_count': 1, 'check_runs': [GOOD_CHECK]}
        with self.assertRaises(LLMToolError) as ctx:
            call(runs, good_checks)
        self.assertEqual(ctx.exception.completion_code, 'CI_WORKFLOW_RUNS_INVALID')

    def test_ci_workflow_runs_invalid_workflow_runs_not_list(self):
        runs = {'total_count': 1, 'workflow_runs': 'x'}
        good_checks = {'total_count': 1, 'check_runs': [GOOD_CHECK]}
        with self.assertRaises(LLMToolError) as ctx:
            call(runs, good_checks)
        self.assertEqual(ctx.exception.completion_code, 'CI_WORKFLOW_RUNS_INVALID')

    def test_ci_evidence_invalid_total_count_mismatch(self):
        good_runs = {'total_count': 1, 'workflow_runs': [GOOD_RUN]}
        checks = {'total_count': 2, 'check_runs': []}
        with self.assertRaises(LLMToolError) as ctx:
            call(good_runs, checks)
        self.assertEqual(ctx.exception.completion_code, 'CI_EVIDENCE_INVALID')

    def test_ci_evidence_invalid_check_run_id_not_int(self):
        good_runs = {'total_count': 1, 'workflow_runs': [GOOD_RUN]}
        checks = {'total_count': 1, 'check_runs': [{'id': 'x'}]}
        with self.assertRaises(LLMToolError) as ctx:
            call(good_runs, checks)
        self.assertEqual(ctx.exception.completion_code, 'CI_EVIDENCE_INVALID')

    def test_ci_evidence_invalid_duplicate_differing_check_runs(self):
        good_runs = {'total_count': 1, 'workflow_runs': [GOOD_RUN]}
        checks = {'total_count': 2, 'check_runs': [{'id': 99, 'a': 1}, {'id': 99, 'a': 2}]}
        with self.assertRaises(LLMToolError) as ctx:
            call(good_runs, checks)
        self.assertEqual(ctx.exception.completion_code, 'CI_EVIDENCE_INVALID')

    def test_ci_evidence_invalid_checks_none(self):
        good_runs = {'total_count': 1, 'workflow_runs': [GOOD_RUN]}
        checks = None
        with self.assertRaises(LLMToolError) as ctx:
            call(good_runs, checks)
        self.assertEqual(ctx.exception.completion_code, 'CI_EVIDENCE_INVALID')


if __name__ == '__main__':
    unittest.main()
