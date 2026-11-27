"""Evidence for the PM's explicit Linux repair after a repeated EC2 timeout."""

import re

from . import ci_failure_overlap, review_repair, task_advance, task_completion
from .review_repair import _refuse


def _positive(value):
    return type(value) is int and value > 0


def _selected_failure(number, head, check_name, get):
    latest, read = {}, {}

    def complete_checks(path):
        value = get(path)
        if path == f'commits/{head}/check-runs?per_page=100':
            checks = value.get('check_runs') if type(value) is dict else None
            if type(checks) is not list or value.get('total_count') != len(checks):
                _refuse('HOSTED_CI_REPAIR_CHECKS_UNAVAILABLE')
        return value

    step, _ = task_advance._review_ci_gate(
        number, head, complete_checks, latest_out=latest, read_out=read)
    if (step != 'CI_FAILED' or read.get('checks_readable') is not True or
            latest.get('name') != check_name or latest.get('conclusion') != 'failure'):
        _refuse('HOSTED_CI_REPAIR_REQUIRED_FAILURE_MISSING')
    check = task_advance._read_check_run(head, check_name, latest['id'], get)
    if check is None:
        _refuse('HOSTED_CI_REPAIR_CHECK_INVALID')
    return check


def _check_identity(check, *, head, name, check_id, app, suite):
    return (type(check) is dict and check.get('id') == check_id and
            check.get('head_sha') == head and check.get('name') == name and
            check.get('status') == 'completed' and check.get('conclusion') == 'failure' and
            type(check.get('app')) is dict and check['app'].get('id') == app and
            type(check.get('check_suite')) is dict and check['check_suite'].get('id') == suite)


def _job_matches(job, *, head, run_id, attempt, slug, workflow):
    job_id = job.get('id')
    return (_positive(job_id) and job.get('run_id') == run_id and
            job.get('run_attempt') == attempt and job.get('head_sha') == head and
            job.get('status') == 'completed' and job.get('conclusion') == 'failure' and
            job.get('check_run_url') == f'https://api.github.com/repos/{slug}/check-runs/{job_id}' and
            task_completion._trusted_ci_runner(
                job, mode='github-hosted', runner_names=workflow['runner_names'],
                runner_labels=workflow['runner_labels']))


def _repo_name(value):
    return value.get('full_name') if type(value) is dict else None


def failure_for_task(task, executor_task, number, *, github_get, job_log_fetch=None):
    """Return the existing CI repair packet input after verifying GitHub evidence."""
    published = task.get('published_pr') if type(task) is dict else None
    if (type(published) is not dict or not _positive(number) or number > 9999999 or
            published.get('pr') != number or
            type(task.get('completion')) is not dict or
            task['completion'].get('executor_task_id') != executor_task):
        _refuse('HOSTED_CI_REPAIR_BINDING_INVALID')
    d6 = task_advance._d6_snapshot(task, executor_task)
    head = d6.get('result_head')
    if (type(head) is not str or re.fullmatch(r'[0-9a-f]{40}', head) is None or
            published.get('head_sha') != head or d6.get('recorded_result_head') != head or
            d6.get('task_id') != executor_task or
            d6.get('state') != 'CLEAN'):
        _refuse('HOSTED_CI_REPAIR_BINDING_INVALID')
    status, _, _ = task_advance._remote_test_status(task, d6)
    if status != 'TIMEOUT' or task_advance._remote_test_timeout_count(task, d6) < 2:
        _refuse('HOSTED_CI_REPAIR_TIMEOUT_STOP_REQUIRED')

    get = task_completion._github_get if github_get is None else github_get
    profile = review_repair._resolve_ci_profile(review_repair._UNSET)
    slug = task_completion._trusted_slug()
    trust = task_completion._ci_trust()
    path = f'.github/workflows/{profile.linux_workflow_file}'
    workflows = [workflow for workflow in trust['workflows']
                 if workflow['path'] == path and workflow['code_check'] == profile.linux_code_check]
    if trust['mode'] != 'github-hosted' or len(workflows) != 1:
        _refuse('HOSTED_CI_REPAIR_WORKFLOW_UNTRUSTED')
    workflow, = workflows
    app = trust['github_actions_app_id']

    def open_pr():
        pr = task_advance._pr_snapshot(number, get)
        if (not task_advance._pr_open_for_head(pr, number, head) or
                pr['head'].get('ref') != d6.get('branch') or
                _repo_name(pr['head'].get('repo')) != slug or
                type(pr.get('base')) is not dict or _repo_name(pr['base'].get('repo')) != slug):
            _refuse('HOSTED_CI_REPAIR_BINDING_INVALID')
        return pr

    pr = open_pr()
    if not _positive(pr.get('changed_files')):
        _refuse('HOSTED_CI_REPAIR_BINDING_INVALID')
    check = _selected_failure(number, head, profile.linux_code_check, get)
    check_id = check['id']
    suite = check.get('check_suite')
    suite_id = suite.get('id') if type(suite) is dict else None
    run_id, job_id = task_advance._ci_run_identity(check)
    if (not _positive(suite_id) or not _positive(run_id) or job_id != check_id or
            not _check_identity(check, head=head, name=profile.linux_code_check,
                                check_id=check_id, app=app, suite=suite_id)):
        _refuse('HOSTED_CI_REPAIR_CHECK_INVALID')
    run_path = f'actions/runs/{run_id}'
    run = get(run_path)
    if (type(run) is not dict or run.get('id') != run_id or run.get('check_suite_id') != suite_id or
            run.get('head_sha') != head or run.get('head_branch') != d6['branch'] or
            run.get('path') != path or run.get('event') != 'pull_request' or
            run.get('status') != 'completed' or run.get('conclusion') != 'failure' or
            _repo_name(run.get('repository')) != slug or _repo_name(run.get('head_repository')) != slug or
            type(run.get('pull_requests')) is not list or
            not any(type(pull) is dict and pull.get('number') == number for pull in run['pull_requests']) or
            not _positive(run.get('run_attempt'))):
        _refuse('HOSTED_CI_REPAIR_RUN_INVALID')
    attempt = run['run_attempt']
    listing = get(f'{run_path}/attempts/{attempt}/jobs?per_page=100')
    jobs = listing.get('jobs') if type(listing) is dict else None
    if (type(jobs) is not list or not 1 <= len(jobs) <= 100 or
            listing.get('total_count') != len(jobs) or any(type(job) is not dict for job in jobs)):
        _refuse('HOSTED_CI_REPAIR_JOBS_UNAVAILABLE')

    aggregate = [job for job in jobs if job.get('id') == job_id]
    shards = [job for job in jobs if type(job.get('name')) is str and
              re.fullmatch(r'linux shard [1-3]/3 \([0-9]+\.[0-9]+\)', job['name']) and
              job.get('conclusion') == 'failure']
    if (len(aggregate) != 1 or aggregate[0].get('name') != profile.linux_code_check or
            len(shards) != 1 or not all(_job_matches(job, head=head, run_id=run_id, attempt=attempt,
                                                   slug=slug, workflow=workflow)
                                        for job in (*aggregate, *shards))):
        _refuse('HOSTED_CI_REPAIR_JOBS_UNAVAILABLE')

    shard, = shards
    shard_id = shard['id']
    shard_check = task_advance._read_check_run(head, shard['name'], shard_id, get)
    if (not _check_identity(shard_check, head=head, name=shard['name'], check_id=shard_id,
                            app=app, suite=suite_id) or
            task_advance._ci_run_identity(shard_check) != (run_id, shard_id)):
        _refuse('HOSTED_CI_REPAIR_CHECK_INVALID')
    text = task_advance._ci_job_log_text(
        (task_advance._read_ci_job_log if job_log_fetch is None else job_log_fetch)(shard_id))
    if text is None:
        _refuse('HOSTED_CI_REPAIR_LOG_UNAVAILABLE')
    failing_tests = task_advance._failing_test_names(text)
    module_names = task_advance._ci_log_failing_modules(text)
    modules = task_advance._resolve_admissible_test_modules(module_names, d6['root'])
    files, files_code = task_advance._pr_files(number, get)
    if (files_code is not None or len(files) != pr['changed_files'] or
            any(type(file) is not dict or type(file.get('filename')) is not str or
                not file['filename'] or ('previous_filename' in file and
                (type(file['previous_filename']) is not str or not file['previous_filename']))
                for file in files)):
        _refuse('HOSTED_CI_REPAIR_FILES_UNAVAILABLE')
    changed = [file[key] for file in files for key in ('filename', 'previous_filename') if key in file]
    declared = task_advance._declared_test_modules(task)
    if (not failing_tests or not modules or len(modules) != len(module_names) or any(
            ci_failure_overlap.overlap([module], changed, declared) != ci_failure_overlap.OVERLAP
            for module in modules)):
        _refuse('HOSTED_CI_REPAIR_OVERLAP_UNPROVEN')
    # Recheck after the log and changed files reads, which can take time.
    final_check = _selected_failure(number, head, profile.linux_code_check, get)
    final_pr = open_pr()
    if final_check != check or get(run_path) != run or final_pr.get('changed_files') != pr.get('changed_files'):
        _refuse('HOSTED_CI_REPAIR_EVIDENCE_CHANGED')
    return {
        'head': head, 'check_name': profile.linux_code_check, 'check_run_id': check_id,
        'conclusion': 'failure', 'excerpt': task_advance._ci_log_excerpt(text),
        'failing_tests': failing_tests, 'failing_modules': modules,
        'workflow_run_id': run_id, 'job_id': shard_id, 'details_unavailable': False,
    }
