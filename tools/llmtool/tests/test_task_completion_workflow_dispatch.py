"""Tests for workflow_dispatch CI evidence collection in task_completion."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest import mock

# Ensure llmtool package directory is importable.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from llmtool.errors import LLMToolError
from llmtool import task_completion

_SLUG = 'davidcatcat/davidcattoolsproject'
_SHA = '0123456789abcdef0123456789abcdef01234567'
_OTHER_SHA = 'fedcba9876543210fedcba9876543210fedcba98'
_BRANCH = 'feature-test'
_OTHER_BRANCH = 'feature-other'
_WORKFLOW_PATH = '.github/workflows/llmtool-linux-ci.yml'
_WORKFLOW_NAME = 'llmtool Linux CI'
_CHECK_NAME = 'check (Linux 3.14)'
_WORKFLOW_ID = 100
_PR_NUMBER = 42
_APP_ID = 15368


def _sample_pr_run(
    *,
    run_id=1001,
    run_number=10,
    run_attempt=1,
    pr_number=_PR_NUMBER,
    head_sha=_SHA,
):
    return {
        'id': run_id,
        'run_number': run_number,
        'run_attempt': run_attempt,
        'workflow_id': _WORKFLOW_ID,
        'path': _WORKFLOW_PATH,
        'event': 'pull_request',
        'head_sha': head_sha,
        'status': 'completed',
        'conclusion': 'success',
        'pull_requests': [
            {
                'number': pr_number,
                'head': {'sha': head_sha},
            }
        ],
    }


def _sample_dispatch_run(
    *,
    run_id=2001,
    run_number=20,
    run_attempt=1,
    workflow_id=_WORKFLOW_ID,
    workflow_path=_WORKFLOW_PATH,
    head_sha=_SHA,
    head_branch=_BRANCH,
    status='completed',
    conclusion='success',
):
    return {
        'id': run_id,
        'run_number': run_number,
        'run_attempt': run_attempt,
        'workflow_id': workflow_id,
        'path': workflow_path,
        'event': 'workflow_dispatch',
        'head_sha': head_sha,
        'head_branch': head_branch,
        'status': status,
        'conclusion': conclusion,
    }


def _sample_job(
    *,
    job_id=3001,
    run_id=2001,
    check_id=4001,
    name=_CHECK_NAME,
    status='completed',
    conclusion='success',
    labels=None,
    runner_group_name='GitHub Actions',
):
    return {
        'id': job_id,
        'run_id': run_id,
        'name': name,
        'status': status,
        'conclusion': conclusion,
        'check_run_url': f'https://api.github.com/repos/{_SLUG}/check-runs/{check_id}',
        'labels': ['ubuntu-24.04'] if labels is None else labels,
        'runner_group_name': runner_group_name,
    }


def _sample_check(
    *,
    check_id=4001,
    name=_CHECK_NAME,
    head_sha=_SHA,
    status='completed',
    conclusion='success',
    app_id=_APP_ID,
):
    return {
        'id': check_id,
        'name': name,
        'head_sha': head_sha,
        'status': status,
        'conclusion': conclusion,
        'app': {
            'id': app_id,
            'slug': 'github-actions',
            'name': 'GitHub Actions',
        },
    }


def _make_fake_github(
    *,
    pr_runs=None,
    dispatch_runs=None,
    jobs=None,
    checks=None,
):
    workflow_data = {
        'id': _WORKFLOW_ID,
        'path': _WORKFLOW_PATH,
        'name': _WORKFLOW_NAME,
        'state': 'active',
    }
    pr_runs_data = {
        'total_count': len(pr_runs) if pr_runs is not None else 0,
        'workflow_runs': pr_runs or [],
    }
    dispatch_runs_data = {
        'total_count': len(dispatch_runs) if dispatch_runs is not None else 0,
        'workflow_runs': dispatch_runs or [],
    }
    jobs_data = {
        'total_count': len(jobs) if jobs is not None else 0,
        'jobs': jobs or [],
    }
    checks_data = {
        'total_count': len(checks) if checks is not None else 0,
        'check_runs': checks or [],
    }

    recorded_paths = []

    def fake_get(path):
        recorded_paths.append(path)
        if path == f'actions/workflows/{_WORKFLOW_PATH.rsplit("/", 1)[-1]}':
            return workflow_data
        if path == f'actions/workflows/{_WORKFLOW_ID}/runs?head_sha={_SHA}&event=pull_request&per_page=100':
            return pr_runs_data
        if path == f'actions/workflows/{_WORKFLOW_ID}/runs?head_sha={_SHA}&event=workflow_dispatch&per_page=100':
            return dispatch_runs_data
        if path.startswith('actions/runs/') and '/jobs?per_page=100' in path:
            return jobs_data
        if path == f'commits/{_SHA}/check-runs?per_page=100':
            return checks_data
        raise AssertionError(f'Unexpected GitHub API path: {path}')

    return fake_get, recorded_paths


def _call_collect(fake_github):
    with mock.patch.object(task_completion, '_trusted_slug', return_value=_SLUG), \
         mock.patch.object(task_completion, '_github_get', side_effect=fake_github):
        return task_completion._collect_one_ci_workflow(
            pr_number=_PR_NUMBER,
            result_sha=_SHA,
            workflow_path=_WORKFLOW_PATH,
            workflow_name=_WORKFLOW_NAME,
            check_name=_CHECK_NAME,
            runner_names=None,
            runner_labels=['ubuntu-24.04'],
            mode='github-hosted',
            app_id=_APP_ID,
            pr_merged=False,
            pr_head_ref=_BRANCH,
        )


class TestTaskCompletionWorkflowDispatch(unittest.TestCase):
    def test_dispatch_run_accepted_on_exact_head_and_branch(self):
        # A workflow_dispatch run on the exact head and branch is accepted.
        fake_gh, recorded = _make_fake_github(
            pr_runs=[],
            dispatch_runs=[_sample_dispatch_run()],
            jobs=[_sample_job()],
            checks=[_sample_check()],
        )
        result = _call_collect(fake_gh)
        self.assertEqual(result['workflow_id'], _WORKFLOW_ID)
        self.assertEqual(result['run_id'], 2001)
        self.assertEqual(result['run_number'], 20)
        self.assertEqual(result['run_attempt'], 1)
        self.assertEqual(result['successful_checks'], [_CHECK_NAME])
        self.assertEqual(result['check_outcomes'], {_CHECK_NAME: 'SUCCESS'})
        self.assertEqual(result['check_ids'], {_CHECK_NAME: 4001})

        pr_query = f'actions/workflows/{_WORKFLOW_ID}/runs?head_sha={_SHA}&event=pull_request&per_page=100'
        dispatch_query = f'actions/workflows/{_WORKFLOW_ID}/runs?head_sha={_SHA}&event=workflow_dispatch&per_page=100'
        self.assertIn(pr_query, recorded)
        self.assertIn(dispatch_query, recorded)

    def test_refused_for_other_branch(self):
        # A workflow_dispatch run on another branch is refused.
        fake_gh, _ = _make_fake_github(
            pr_runs=[],
            dispatch_runs=[_sample_dispatch_run(head_branch=_OTHER_BRANCH)],
        )
        result = _call_collect(fake_gh)
        self.assertIsNone(result['run_id'])
        self.assertEqual(result['successful_checks'], [])
        self.assertEqual(result['check_outcomes'], {_CHECK_NAME: 'MISSING'})

    def test_refused_for_other_sha(self):
        # A workflow_dispatch run on another sha is refused.
        fake_gh, _ = _make_fake_github(
            pr_runs=[],
            dispatch_runs=[_sample_dispatch_run(head_sha=_OTHER_SHA)],
        )
        result = _call_collect(fake_gh)
        self.assertIsNone(result['run_id'])
        self.assertEqual(result['successful_checks'], [])
        self.assertEqual(result['check_outcomes'], {_CHECK_NAME: 'MISSING'})

    def test_refused_for_other_workflow(self):
        # A run from another workflow id or path is refused.
        fake_gh, _ = _make_fake_github(
            pr_runs=[],
            dispatch_runs=[_sample_dispatch_run(workflow_id=999)],
        )
        result = _call_collect(fake_gh)
        self.assertIsNone(result['run_id'])
        self.assertEqual(result['successful_checks'], [])
        self.assertEqual(result['check_outcomes'], {_CHECK_NAME: 'MISSING'})

    def test_pull_request_run_still_preferred(self):
        # An existing pull_request run is preferred over workflow_dispatch.
        pr_run = _sample_pr_run(run_id=1001)
        dispatch_run = _sample_dispatch_run(run_id=2001)
        fake_gh, recorded = _make_fake_github(
            pr_runs=[pr_run],
            dispatch_runs=[dispatch_run],
            jobs=[_sample_job(run_id=1001)],
            checks=[_sample_check()],
        )
        result = _call_collect(fake_gh)
        self.assertEqual(result['run_id'], 1001)
        dispatch_query = f'actions/workflows/{_WORKFLOW_ID}/runs?head_sha={_SHA}&event=workflow_dispatch&per_page=100'
        self.assertNotIn(dispatch_query, recorded)

    def test_failed_dispatch_job_not_success(self):
        # A failed workflow_dispatch job is not considered successful.
        fake_gh, _ = _make_fake_github(
            pr_runs=[],
            dispatch_runs=[_sample_dispatch_run()],
            jobs=[_sample_job(conclusion='failure')],
            checks=[_sample_check(conclusion='failure')],
        )
        result = _call_collect(fake_gh)
        self.assertEqual(result['run_id'], 2001)
        self.assertEqual(result['successful_checks'], [])
        self.assertEqual(result['check_outcomes'], {_CHECK_NAME: 'FAILED'})

    def test_cancelled_dispatch_job_not_success(self):
        # A cancelled workflow_dispatch job is not considered successful.
        fake_gh, _ = _make_fake_github(
            pr_runs=[],
            dispatch_runs=[_sample_dispatch_run()],
            jobs=[_sample_job(conclusion='cancelled')],
            checks=[_sample_check(conclusion='cancelled')],
        )
        result = _call_collect(fake_gh)
        self.assertEqual(result['successful_checks'], [])
        self.assertEqual(result['check_outcomes'], {_CHECK_NAME: 'FAILED'})

    def test_pending_dispatch_job_not_success(self):
        # An in-progress workflow_dispatch job reports pending outcome.
        fake_gh, _ = _make_fake_github(
            pr_runs=[],
            dispatch_runs=[_sample_dispatch_run()],
            jobs=[_sample_job(status='in_progress', conclusion=None)],
            checks=[_sample_check(status='in_progress', conclusion=None)],
        )
        result = _call_collect(fake_gh)
        self.assertEqual(result['successful_checks'], [])
        self.assertEqual(result['check_outcomes'], {_CHECK_NAME: 'PENDING'})

    def test_no_runs_returns_missing(self):
        # When neither pull_request nor workflow_dispatch has runs, return missing.
        fake_gh, _ = _make_fake_github(
            pr_runs=[],
            dispatch_runs=[],
        )
        result = _call_collect(fake_gh)
        self.assertIsNone(result['run_id'])
        self.assertEqual(result['successful_checks'], [])
        self.assertEqual(result['check_outcomes'], {_CHECK_NAME: 'MISSING'})

    def test_failing_fallback_query_returns_missing(self):
        # A failing workflow_dispatch fallback query behaves as missing evidence.
        workflow_data = {
            'id': _WORKFLOW_ID,
            'path': _WORKFLOW_PATH,
            'name': _WORKFLOW_NAME,
            'state': 'active',
        }
        pr_runs_data = {
            'total_count': 0,
            'workflow_runs': [],
        }

        def fake_get(path):
            if path == f'actions/workflows/{_WORKFLOW_PATH.rsplit("/", 1)[-1]}':
                return workflow_data
            if path == f'actions/workflows/{_WORKFLOW_ID}/runs?head_sha={_SHA}&event=pull_request&per_page=100':
                return pr_runs_data
            if path == f'actions/workflows/{_WORKFLOW_ID}/runs?head_sha={_SHA}&event=workflow_dispatch&per_page=100':
                task_completion._stop('GITHUB_EVIDENCE_UNAVAILABLE')
            raise AssertionError(f'Unexpected GitHub API path: {path}')

        result = _call_collect(fake_get)
        self.assertIsNone(result['run_id'])
        self.assertEqual(result['successful_checks'], [])
        self.assertEqual(result['check_outcomes'], {_CHECK_NAME: 'MISSING'})

    def test_fallback_response_holding_pull_request_runs_ignored(self):
        # A fallback response holding pull_request runs is ignored.
        fake_gh, _ = _make_fake_github(
            pr_runs=[],
            dispatch_runs=[_sample_pr_run()],
        )
        result = _call_collect(fake_gh)
        self.assertIsNone(result['run_id'])
        self.assertEqual(result['successful_checks'], [])
        self.assertEqual(result['check_outcomes'], {_CHECK_NAME: 'MISSING'})

    def test_fallback_response_not_valid_runs_list_ignored(self):
        # A fallback response that is not a valid runs list is treated as no dispatch run.
        workflow_data = {
            'id': _WORKFLOW_ID,
            'path': _WORKFLOW_PATH,
            'name': _WORKFLOW_NAME,
            'state': 'active',
        }
        pr_runs_data = {
            'total_count': 0,
            'workflow_runs': [],
        }

        def fake_get(path):
            if path == f'actions/workflows/{_WORKFLOW_PATH.rsplit("/", 1)[-1]}':
                return workflow_data
            if path == f'actions/workflows/{_WORKFLOW_ID}/runs?head_sha={_SHA}&event=pull_request&per_page=100':
                return pr_runs_data
            if path == f'actions/workflows/{_WORKFLOW_ID}/runs?head_sha={_SHA}&event=workflow_dispatch&per_page=100':
                return {'unexpected': 'shape'}
            raise AssertionError(f'Unexpected GitHub API path: {path}')

        result = _call_collect(fake_get)
        self.assertIsNone(result['run_id'])
        self.assertEqual(result['successful_checks'], [])
        self.assertEqual(result['check_outcomes'], {_CHECK_NAME: 'MISSING'})

    def test_multiple_dispatch_runs_only_fully_matching_candidate(self):
        # Only fully matching workflow_dispatch runs become candidates.
        mismatched_branch = _sample_dispatch_run(run_id=2001, head_branch=_OTHER_BRANCH)
        mismatched_sha = _sample_dispatch_run(run_id=2002, head_sha=_OTHER_SHA)
        mismatched_workflow = _sample_dispatch_run(run_id=2003, workflow_id=999)
        matching = _sample_dispatch_run(run_id=2004)
        fake_gh, _ = _make_fake_github(
            pr_runs=[],
            dispatch_runs=[mismatched_branch, mismatched_sha, mismatched_workflow, matching],
            jobs=[_sample_job(run_id=2004)],
            checks=[_sample_check()],
        )
        result = _call_collect(fake_gh)
        self.assertEqual(result['run_id'], 2004)
        self.assertEqual(result['successful_checks'], [_CHECK_NAME])
        self.assertEqual(result['check_outcomes'], {_CHECK_NAME: 'SUCCESS'})


if __name__ == '__main__':
    unittest.main()
