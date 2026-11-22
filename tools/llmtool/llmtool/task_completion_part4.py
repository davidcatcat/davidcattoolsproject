"""Git tree entries, test classification and CI evidence for task_completion.

Part of the #2281 split. The code is moved verbatim. Parent helpers are
resolved lazily through task_completion so patching that module keeps effect.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

from .errors import LLMToolError

# #2281 A2: the parent module is already in sys.modules while it imports this
# part, so constant reads through it keep a patch on the task_completion path
# effective here too.
_m = sys.modules[__package__ + '.task_completion']
_UNSET = object()


def _stop(*args, **kwargs):
    from . import task_completion as _m
    return _m._stop(*args, **kwargs)


def _git(*args, **kwargs):
    from . import task_completion as _m
    return _m._git(*args, **kwargs)


def _github_get(*args, **kwargs):
    from . import task_completion as _m
    return _m._github_get(*args, **kwargs)


def _trusted_slug(*args, **kwargs):
    from . import task_completion as _m
    return _m._trusted_slug(*args, **kwargs)


def _ci_trust(*args, **kwargs):
    from . import task_completion as _m
    return _m._ci_trust(*args, **kwargs)


def _docs_only_ci(*args, **kwargs):
    from . import task_completion as _m
    return _m._docs_only_ci(*args, **kwargs)


def _trusted_ci_runner(*args, **kwargs):
    from . import task_completion as _m
    return _m._trusted_ci_runner(*args, **kwargs)


def _parent_collect_one_ci_workflow(*args, **kwargs):
    from . import task_completion as _m
    return _m._collect_one_ci_workflow(*args, **kwargs)


def _tree_blob(*args, **kwargs):
    from . import task_completion as _m
    return _m._tree_blob(*args, **kwargs)


def _proposal_blob_size(*args, **kwargs):
    from . import task_completion as _m
    return _m._proposal_blob_size(*args, **kwargs)





def verify_artifact_applied(root, artifact, artifact_sha256, source_sha, result_sha,
                            changed_paths, write_paths, *, scope_paths=None):
    """Apply the proposal in a bounded temp tree and compare result blobs/modes."""
    if (type(artifact) is not str or not artifact or
            len(artifact.encode('utf-8')) > _m._GIT_LIMIT or
            type(artifact_sha256) is not str or
            hashlib.sha256(artifact.encode('utf-8')).hexdigest() != artifact_sha256 or
            type(source_sha) is not str or _m._HEX40.fullmatch(source_sha) is None or
            type(result_sha) is not str or _m._HEX40.fullmatch(result_sha) is None):
        _stop('DEVELOPER_ARTIFACT_BINDING_INVALID')
    patch_paths = []
    for line in artifact.splitlines():
        if line.startswith('diff --git '):
            match = re.fullmatch(r'diff --git a/([^\s]+) b/([^\s]+)', line)
            if match is None or match.group(1) != match.group(2):
                _stop('DEVELOPER_ARTIFACT_INVALID')
            patch_paths.append(match.group(1))
    scope_paths = patch_paths if scope_paths is None else scope_paths
    if (not patch_paths or len(patch_paths) > _m._PROPOSAL_FILE_LIMIT or
            len(patch_paths) != len(set(patch_paths)) or
            set(patch_paths) != set(changed_paths) or
            not set(scope_paths).issubset(set(write_paths))):
        _stop('DEVELOPER_ARTIFACT_RESULT_MISMATCH')
    for path in patch_paths:
        parts = Path(path).parts
        if (Path(path).is_absolute() or not parts or
                any(part in ('', '.', '..') for part in parts) or '\\' in path or
                '\x00' in path):
            _stop('DEVELOPER_ARTIFACT_INVALID')
    total_blob_bytes = 0
    for path in patch_paths:
        total_blob_bytes += _proposal_blob_size(root, source_sha, path)
        total_blob_bytes += _proposal_blob_size(root, result_sha, path)
        if total_blob_bytes > _m._PROPOSAL_TREE_LIMIT:
            _stop('DEVELOPER_ARTIFACT_TREE_TOO_LARGE')
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_ATTR_NOSYSTEM': '1', 'GIT_OPTIONAL_LOCKS': '0',
           'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_LAZY_FETCH': '1',
           'GIT_NO_REPLACE_OBJECTS': '1'}
    try:
        with tempfile.TemporaryDirectory(prefix='llmtool-proposal-tree-') as temporary:
            temp_root = Path(temporary)
            for path in patch_paths:
                source = _tree_blob(root, source_sha, path)
                if source is None:
                    continue
                mode, content = source
                target = temp_root.joinpath(*Path(path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream:
                    stream.write(content)
                target.chmod(0o755 if mode == '100755' else 0o644)
            proc = subprocess.run(
                ['/usr/bin/git', '-c', 'core.useReplaceRefs=false',
                 '-c', 'core.hooksPath=/dev/null', 'apply',
                 '--binary', '--whitespace=nowarn', '-'],
                cwd=temp_root, input=artifact.encode('utf-8'),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_m._TIMEOUT, env=env, shell=False, check=False)
            if proc.returncode != 0:
                _stop('DEVELOPER_ARTIFACT_NOT_APPLIED')
            for path in patch_paths:
                expected = _tree_blob(root, result_sha, path)
                target = temp_root.joinpath(*Path(path).parts)
                try:
                    actual_stat = target.lstat()
                except FileNotFoundError:
                    actual_stat = None
                if expected is None:
                    if actual_stat is not None:
                        _stop('DEVELOPER_ARTIFACT_RESULT_TREE_MISMATCH')
                    continue
                mode, content = expected
                if (actual_stat is None or not stat.S_ISREG(actual_stat.st_mode) or
                        target.read_bytes() != content or
                        bool(actual_stat.st_mode & 0o111) != (mode == '100755')):
                    _stop('DEVELOPER_ARTIFACT_RESULT_TREE_MISMATCH')
    except LLMToolError:
        raise
    except Exception:
        _stop('DEVELOPER_ARTIFACT_CHECK_UNKNOWN')


def _collect_one_ci_workflow(*, pr_number, result_sha, workflow_path,
                             workflow_name, check_name, runner_names,
                             runner_labels=None, mode='self-hosted',
                             app_id=_UNSET,
                             pr_merged=False, pr_head_ref=None):
    if app_id is _UNSET:
        app_id = _m._GITHUB_ACTIONS_APP_ID
    if type(app_id) is not int or app_id != _m._GITHUB_ACTIONS_APP_ID:
        _stop('TRUST_CI_INVALID')
    slug = _trusted_slug()
    workflow = _github_get('actions/workflows/' + workflow_path.rsplit('/', 1)[-1])
    if (type(workflow) is not dict or type(workflow.get('id')) is not int or
            workflow['id'] <= 0 or workflow.get('path') != workflow_path or
            workflow.get('name') != workflow_name or workflow.get('state') != 'active'):
        _stop('CI_WORKFLOW_MISMATCH')
    workflow_id = workflow['id']
    runs = _github_get(
        f'actions/workflows/{workflow_id}/runs?head_sha={result_sha}&event=pull_request&per_page=100')
    if (type(runs) is not dict or type(runs.get('workflow_runs')) is not list or
            type(runs.get('total_count')) is not int or
            runs['total_count'] != len(runs['workflow_runs']) or
            len(runs['workflow_runs']) > 100):
        _stop('CI_WORKFLOW_RUNS_INVALID')
    candidates = []
    for run in runs['workflow_runs']:
        if type(run) is not dict:
            _stop('CI_WORKFLOW_RUNS_INVALID')
        pull_requests = run.get('pull_requests')
        matching_pr = (type(pull_requests) is list and
                       any(type(item) is dict and item.get('number') == pr_number and
                           type(item.get('head')) is dict and
                           item['head'].get('sha') == result_sha for item in pull_requests))
        merged_empty_prs_match = (
            pr_merged is True and type(pull_requests) is list and not pull_requests and
            run.get('head_branch') == pr_head_ref and
            type(run.get('head_repository')) is dict and
            run['head_repository'].get('full_name') == slug)
        if (run.get('workflow_id') != workflow_id or run.get('path') != workflow_path or
                run.get('event') != 'pull_request' or run.get('head_sha') != result_sha or
                not (matching_pr or merged_empty_prs_match)):
            _stop('CI_WORKFLOW_RUN_MISMATCH')
        if (type(run.get('id')) is not int or run['id'] <= 0 or
                type(run.get('run_number')) is not int or run['run_number'] <= 0 or
                type(run.get('run_attempt')) is not int or run['run_attempt'] <= 0):
            _stop('CI_WORKFLOW_RUNS_INVALID')
        candidates.append(run)
    if not candidates and type(pr_head_ref) is str and pr_head_ref:
        # Fall back to workflow_dispatch runs when no pull_request run exists.
        try:
            dispatch_runs = _github_get(
                f'actions/workflows/{workflow_id}/runs?head_sha={result_sha}&event=workflow_dispatch&per_page=100')
        except Exception:
            dispatch_runs = None
        if (type(dispatch_runs) is dict and
                type(dispatch_runs.get('workflow_runs')) is list and
                type(dispatch_runs.get('total_count')) is int and
                dispatch_runs['total_count'] == len(dispatch_runs['workflow_runs']) and
                len(dispatch_runs['workflow_runs']) <= 100):
            for run in dispatch_runs['workflow_runs']:
                if (type(run) is dict and
                        run.get('workflow_id') == workflow_id and
                        run.get('path') == workflow_path and
                        run.get('event') == 'workflow_dispatch' and
                        run.get('head_sha') == result_sha and
                        type(pr_head_ref) is str and pr_head_ref and
                        run.get('head_branch') == pr_head_ref and
                        type(run.get('id')) is int and run['id'] > 0 and
                        type(run.get('run_number')) is int and run['run_number'] > 0 and
                        type(run.get('run_attempt')) is int and run['run_attempt'] > 0):
                    candidates.append(run)
    if not candidates:
        return {'workflow_id': workflow_id, 'run_id': None, 'run_attempt': None,
                'run_number': None, 'successful_checks': [], 'check_ids': {},
                'check_outcomes': {check_name: 'MISSING'}}
    run = max(candidates, key=lambda item: (item['run_number'], item['run_attempt'], item['id']))
    run_id, attempt = run['id'], run['run_attempt']
    jobs = _github_get(f'actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100')
    if (type(jobs) is not dict or type(jobs.get('jobs')) is not list or
            type(jobs.get('total_count')) is not int or
            jobs['total_count'] != len(jobs['jobs']) or len(jobs['jobs']) > 100):
        _stop('CI_WORKFLOW_JOBS_INVALID')
    required_jobs = {}
    for job in jobs['jobs']:
        if type(job) is not dict:
            _stop('CI_WORKFLOW_JOBS_INVALID')
        if job.get('name') != check_name:
            continue
        check_url = job.get('check_run_url')
        check_id_match = (None if type(check_url) is not str else re.fullmatch(
            r'https://api\.github\.com/repos/' + re.escape(slug) + r'/'
            r'check-runs/([1-9][0-9]{0,18})', check_url))
        if (check_name in required_jobs or job.get('run_id') != run_id or
                not _trusted_ci_runner(job, mode=mode, runner_names=runner_names,
                                       runner_labels=runner_labels) or
                type(job.get('id')) is not int or job['id'] <= 0 or
                check_id_match is None):
            _stop('CI_WORKFLOW_JOBS_INVALID')
        required_jobs[check_name] = (job, int(check_id_match.group(1)))
    if set(required_jobs) != {check_name}:
        return {'workflow_id': workflow_id, 'run_id': run_id, 'run_attempt': attempt,
                'run_number': run['run_number'], 'successful_checks': [], 'check_ids': {},
                'check_outcomes': {check_name: 'MISSING'}}
    checks = _github_get(f'commits/{result_sha}/check-runs?per_page=100')
    if (type(checks) is not dict or type(checks.get('check_runs')) is not list or
            type(checks.get('total_count')) is not int or
            checks['total_count'] != len(checks['check_runs']) or
            len(checks['check_runs']) > 100):
        _stop('CI_EVIDENCE_INVALID')
    by_id = {}
    for check in checks['check_runs']:
        if type(check) is not dict or type(check.get('id')) is not int:
            _stop('CI_EVIDENCE_INVALID')
        if check['id'] in by_id and by_id[check['id']] != check:
            _stop('CI_EVIDENCE_INVALID')
        by_id[check['id']] = check
    successful, check_ids, check_outcomes = set(), {}, {}
    job, check_id = required_jobs[check_name]
    check = by_id.get(check_id)
    app = None if check is None else check.get('app')
    if (check is not None and check.get('name') == check_name and
            check.get('head_sha') == result_sha and type(app) is dict and
            app.get('id') == app_id and app.get('slug') == 'github-actions' and
            app.get('name') == 'GitHub Actions' and
            job.get('status') == 'completed' and job.get('conclusion') == 'success' and
            check.get('status') == 'completed' and check.get('conclusion') == 'success' and
            run.get('status') == 'completed' and run.get('conclusion') == 'success'):
        successful.add(check_name)
        check_ids[check_name] = check['id']
        check_outcomes[check_name] = 'SUCCESS'
    elif (check is None or check.get('name') != check_name or
          check.get('head_sha') != result_sha or type(app) is not dict or
          app.get('id') != app_id or app.get('slug') != 'github-actions' or
          app.get('name') != 'GitHub Actions'):
        check_outcomes[check_name] = 'MISSING'
    elif (run.get('status') != 'completed' or job.get('status') != 'completed' or
          check.get('status') != 'completed'):
        check_outcomes[check_name] = 'PENDING'
    elif (run.get('conclusion') in ('success', 'failure', 'cancelled', 'timed_out',
                                     'action_required', 'startup_failure', 'stale',
                                     'neutral') and
          job.get('conclusion') in ('success', 'failure', 'cancelled', 'timed_out',
                                     'action_required', 'startup_failure', 'stale',
                                     'neutral') and
          check.get('conclusion') in ('success', 'failure', 'cancelled', 'timed_out',
                                       'action_required', 'startup_failure', 'stale',
                                       'neutral')):
        check_outcomes[check_name] = 'FAILED'
    else:
        check_outcomes[check_name] = 'MISSING'
    return {'workflow_id': workflow_id, 'run_id': run_id, 'run_attempt': attempt,
            'run_number': run['run_number'], 'successful_checks': sorted(successful),
            'check_ids': check_ids, 'check_outcomes': check_outcomes}


def _collect_ci_evidence(*, pr_number, result_sha, changed_paths=None,
                         pr_merged=False, pr_head_ref=None):
    """Exact-head CI evidence for every trusted workflow on the selected path.

    Code path: every ci_trust workflow with a ``code_check`` must succeed and
    the first such workflow is primary; the docs-only path does the same with
    ``docs_check``. With the Owner's values this is the Mac + Linux code gate
    and the Linux ``check (docs)`` docs gate.
    """
    ci_trust = _ci_trust()
    docs_only = _docs_only_ci(changed_paths)
    linux_required = True
    field = 'docs_check' if docs_only else 'code_check'
    selected = [workflow for workflow in ci_trust['workflows']
                if workflow[field] is not None]
    # #1558: like task_advance._merge_checks, Mac only for Mac-workflow paths.
    if (not docs_only and type(changed_paths) is list and changed_paths and
            all(type(path) is str for path in changed_paths)):
        from . import task_advance  # imported here: task_advance imports this module
        if not task_advance._mac_ci_runs(changed_paths):
            selected = [workflow for workflow in selected
                        if workflow[field] != _m._MAC_CHECK]
    if not selected:
        _stop('TRUST_CI_INVALID')
    results = []
    for index, workflow in enumerate(selected):
        try:
            result = _parent_collect_one_ci_workflow(
                pr_number=pr_number, result_sha=result_sha,
                workflow_path=workflow['path'], workflow_name=workflow['name'],
                check_name=workflow[field], runner_names=workflow['runner_names'],
                runner_labels=workflow['runner_labels'], mode=ci_trust['mode'],
                app_id=ci_trust['github_actions_app_id'], pr_merged=pr_merged,
                pr_head_ref=pr_head_ref)
        except LLMToolError as exc:
            # A malformed Mac job can otherwise mask a completed Linux failure.
            # Read the later required workflow only to surface that failure; if
            # it is not confirmed, preserve the original fail-closed error.
            if (index != 0 or getattr(exc, 'completion_code', None) !=
                    'CI_WORKFLOW_JOBS_INVALID' or
                    len(selected) < 2):
                raise
            later = []
            for later_workflow in selected[index + 1:]:
                try:
                    result = _parent_collect_one_ci_workflow(
                        pr_number=pr_number, result_sha=result_sha,
                        workflow_path=later_workflow['path'],
                        workflow_name=later_workflow['name'],
                        check_name=later_workflow[field],
                        runner_names=later_workflow['runner_names'],
                        runner_labels=later_workflow['runner_labels'],
                        mode=ci_trust['mode'], app_id=ci_trust['github_actions_app_id'],
                        pr_merged=pr_merged, pr_head_ref=pr_head_ref)
                except LLMToolError:
                    # A secondary workflow cannot replace the original refusal
                    # unless it independently supplies trusted FAILED evidence.
                    continue
                later.append((later_workflow, result))
                if 'FAILED' in result['check_outcomes'].values():
                    break
            if not later or not any(
                    'FAILED' in item['check_outcomes'].values()
                    for _, item in later):
                raise
            successful = set()
            check_ids = {}
            check_outcomes = {}
            for _, item in later:
                successful.update(item['successful_checks'])
                check_ids.update(item['check_ids'])
                check_outcomes.update(item['check_outcomes'])
            primary_workflow, primary = next(
                (workflow, item) for workflow, item in later
                if 'FAILED' in item['check_outcomes'].values())
            return {'workflow_id': primary['workflow_id'], 'run_id': primary['run_id'],
                    'run_attempt': primary['run_attempt'], 'run_number': primary['run_number'],
                    'successful_checks': sorted(successful), 'check_ids': check_ids,
                    'check_outcomes': check_outcomes, 'linux_required': linux_required,
                    'docs_only': docs_only,
                    'required_checks': sorted(item[field] for item in selected),
                    'workflow_path': primary_workflow['path'],
                    'workflow_name': primary_workflow['name']}
        results.append(result)
    successful, check_ids, check_outcomes = set(), {}, {}
    for result in results:
        successful.update(result['successful_checks'])
        check_ids.update(result['check_ids'])
        check_outcomes.update(result['check_outcomes'])
    primary, primary_workflow = results[0], selected[0]
    return {'workflow_id': primary['workflow_id'], 'run_id': primary['run_id'],
            'run_attempt': primary['run_attempt'], 'run_number': primary['run_number'],
            'successful_checks': sorted(successful), 'check_ids': check_ids,
            'check_outcomes': check_outcomes,
            'linux_required': linux_required, 'docs_only': docs_only,
            'required_checks': sorted(workflow[field] for workflow in selected),
            'workflow_path': primary_workflow['path'],
            'workflow_name': primary_workflow['name']}
