"""Main FULL Mac suite trigger for ``llmtool ci full-mac-due`` (Owner #1485 P1).

One FULL is dispatched only when all four Owner conditions hold: main has a SHA
FULL has not verified, the last main merge (GitHub merge time) is at least
QUIET_SECONDS old, no main FULL is running or queued, and the main SHA has no
trusted FULL success. Every value is read from GitHub, so a pause loses nothing.
A FULL run tests its own head_sha (the workflow checks out github.sha).
"""

from __future__ import annotations

from dataclasses import fields
import datetime
import json
import os
import re
import sys
import time

from . import ci_profile

_UNSET = object()
_DEFAULT_PROFILE = ci_profile.default_profile()

QUIET_SECONDS = 1800
SHARD_JOBS = ('shards (1/3)', 'shards (2/3)', 'shards (3/3)')
RUNS = (f'actions/workflows/{_DEFAULT_PROFILE.macos_workflow_file}'
        '/runs?branch=main&event=workflow_dispatch&per_page=50')
JOBS = SHARD_JOBS + (_DEFAULT_PROFILE.macos_check,)
ACTIVE = frozenset({'queued', 'waiting', 'pending', 'requested', 'in_progress'})
FAILED = frozenset({'failure', 'timed_out', 'startup_failure'})
SHA = re.compile(r'[0-9a-f]{40}\Z')
DUE = 'MAC_FULL_SUITE_DUE'
DISPATCHED = 'MAC_FULL_SUITE_DISPATCHED'
ACTIVE_RUN = 'MAC_FULL_SUITE_ALREADY_QUEUED'
VERIFIED = 'MAC_FULL_SUITE_VERIFIED'
QUIET = 'MAC_FULL_SUITE_QUIET'
FULL_FAILED = 'MAC_FULL_SUITE_FAILED'
DISPATCH_FAILED = 'MAC_FULL_SUITE_DISPATCH_FAILED'
UNAVAILABLE = 'MAC_FULL_SUITE_STATE_UNAVAILABLE'
PROFILE_INVALID = 'MAC_FULL_SUITE_PROFILE_INVALID'
OK_CODES = (DISPATCHED, ACTIVE_RUN, VERIFIED, QUIET)


class StateError(Exception):
    """A GitHub read failed or returned an unexpected shape."""


class ProfileError(StateError):
    """The CI profile is missing, invalid or ambiguous."""


def _is_plain_workflow_name(value):
    return ('/' not in value and '\\' not in value and '?' not in value
            and '#' not in value and value not in ('.', '..'))


def resolve_profile(profile=_UNSET):
    """Return a validated CiProfile or raise ProfileError."""
    if profile is _UNSET:
        try:
            candidate = ci_profile.default_profile()
        except Exception:
            raise ProfileError('ci_profile') from None
    else:
        candidate = profile
    if not isinstance(candidate, ci_profile.CiProfile):
        raise ProfileError('ci_profile')
    for field in fields(ci_profile.CiProfile):
        value = getattr(candidate, field.name, None)
        try:
            ci_profile._validate_name(value)
        except ci_profile.CiProfileError:
            raise ProfileError('ci_profile') from None
    if not (_is_plain_workflow_name(candidate.linux_workflow_file)
            and _is_plain_workflow_name(candidate.macos_workflow_file)):
        raise ProfileError('ci_profile')
    if candidate.linux_workflow_file == candidate.macos_workflow_file:
        raise ProfileError('ci_profile')
    if candidate.macos_check in SHARD_JOBS:
        raise ProfileError('ci_profile')
    return candidate


def runs_path(workflow_file):
    """Return the GitHub workflow runs API path for one Mac workflow file."""
    return (f'actions/workflows/{workflow_file}'
            '/runs?branch=main&event=workflow_dispatch&per_page=50')


def full_jobs(profile):
    """Return the required job names for a FULL Mac run under profile."""
    return SHARD_JOBS + (profile.macos_check,)


def _get(github_get, path):
    try:
        data = github_get(path)
    except Exception:
        raise StateError(path) from None
    if not isinstance(data, (dict, list)):
        raise StateError(path)
    return data


def _epoch(value):
    try:
        return datetime.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=datetime.timezone.utc).timestamp()
    except (TypeError, ValueError):
        raise StateError('time') from None


def main_state(github_get):
    """Return (main SHA, epoch of the GitHub merge that made it)."""
    branch = _get(github_get, 'branches/main')
    commit = branch.get('commit') if isinstance(branch, dict) else None
    sha = commit.get('sha') if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not SHA.match(sha):
        raise StateError('branches/main')
    pulls = _get(github_get, f'commits/{sha}/pulls')
    if not isinstance(pulls, list):
        raise StateError('pulls')
    merged = [pull.get('merged_at') for pull in pulls if isinstance(pull, dict)
              and pull.get('merge_commit_sha') == sha and pull.get('merged_at')]
    if merged:
        return sha, max(_epoch(value) for value in merged)
    try:
        return sha, _epoch(commit['commit']['committer']['date'])
    except (KeyError, TypeError):
        raise StateError('merge time') from None


def full_runs(github_get, *, profile=_UNSET):
    """Return main workflow_dispatch runs of the Mac workflow, newest first."""
    resolved = resolve_profile(profile)
    path = runs_path(resolved.macos_workflow_file)
    data = _get(github_get, path)
    runs = data.get('workflow_runs') if isinstance(data, dict) else None
    if not isinstance(runs, list) or not all(
            isinstance(run, dict) and isinstance(run.get('status'), str) for run in runs):
        raise StateError(path)
    return runs


def _all_jobs(github_get, run_id):
    """Return every job record for run_id, reading all pages and failing closed."""
    jobs = []
    total = None
    page = 1
    while True:
        suffix = '' if page == 1 else f'&page={page}'
        data = _get(github_get, f'actions/runs/{run_id}/jobs?per_page=100{suffix}')
        page_jobs = data.get('jobs') if isinstance(data, dict) else None
        if not isinstance(page_jobs, list):
            raise StateError('jobs')
        reported = data.get('total_count')
        if reported is not None:
            if type(reported) is not int or reported < 0:
                raise StateError('jobs')
            total = reported
        jobs.extend(page_jobs)
        if total is not None:
            if len(jobs) == total:
                return jobs
            if len(jobs) > total or len(page_jobs) < 100:
                # The response disagrees with total_count: refuse trust.
                raise StateError('jobs')
        elif len(page_jobs) < 100:
            return jobs
        page += 1


def trusted(github_get, run, *, profile=_UNSET):
    """A success counts only when every required job has exactly one success record."""
    resolved = resolve_profile(profile)
    if run.get('status') != 'completed' or run.get('conclusion') != 'success':
        return False
    run_id = run.get('id')
    if type(run_id) is not int:
        raise StateError('run id')
    jobs = _all_jobs(github_get, run_id)
    required = set(full_jobs(resolved))
    conclusions = {}
    for job in jobs:
        if not isinstance(job, dict):
            continue
        name = job.get('name')
        if name not in required:
            continue
        if name in conclusions:
            # Two records for one required job disagree or are ambiguous: fail closed.
            return False
        conclusions[name] = job.get('conclusion')
    return all(conclusions.get(name) == 'success' for name in required)


def sha_result(github_get, runs, sha, skip_id=None, *, profile=_UNSET):
    """Return PASS, FAIL or None from the newest finished FULL run of sha."""
    resolved = resolve_profile(profile)
    for run in runs:
        if run.get('id') == skip_id or run.get('head_sha') != sha or run['status'] != 'completed':
            continue
        if trusted(github_get, run, profile=resolved):
            return 'PASS'
        if run.get('conclusion') in FAILED:
            return 'FAIL'
    return None


def decide(github_get, *, now, quiet_seconds=QUIET_SECONDS, profile=_UNSET):
    """Return (code, detail) from GitHub state; never writes."""
    resolved = resolve_profile(profile)
    sha, merged_at = main_state(github_get)
    runs = full_runs(github_get, profile=resolved)
    detail = {'main_sha': sha, 'merge_age_seconds': int(now - merged_at)}
    finished = [run for run in runs if run['status'] == 'completed' and
                run.get('conclusion') in FAILED | {'success'}]
    if finished and finished[0].get('conclusion') in FAILED:
        detail['alert'] = {'sha': finished[0].get('head_sha'), 'run': finished[0].get('html_url'),
                           'conclusion': finished[0].get('conclusion')}
    active = [run.get('id') for run in runs if run['status'].lower() in ACTIVE]
    if active:
        return ACTIVE_RUN, dict(detail, active_runs=active)
    result = sha_result(github_get, runs, sha, profile=resolved)
    if result == 'PASS':
        return VERIFIED, detail
    if result == 'FAIL':
        return FULL_FAILED, detail
    if quiet_seconds > 0 and now - merged_at < quiet_seconds:
        return QUIET, detail
    return DUE, detail


def request_full(gh_runner, root, github_get, *, now=None, quiet_seconds=QUIET_SECONDS,
                 profile=_UNSET):
    """Dispatch one FULL on main when due; return (code, detail)."""
    now = time.time() if now is None else now
    try:
        resolved = resolve_profile(profile)
        code, detail = decide(github_get, now=now, quiet_seconds=quiet_seconds,
                              profile=resolved)
        if code != DUE:
            return code, detail
        # Re-read just before dispatch so a run from an overlapping sweep is seen.
        if any(run['status'].lower() in ACTIVE
               for run in full_runs(github_get, profile=resolved)):
            return ACTIVE_RUN, detail
    except ProfileError:
        return PROFILE_INVALID, {'profile': 'invalid'}
    except StateError as exc:
        return UNAVAILABLE, {'read': str(exc)}
    try:
        result = gh_runner(
            root, ['workflow', 'run', resolved.macos_workflow_file, '--ref', 'main'])
        dispatched = getattr(result, 'returncode', 1) == 0
    except Exception:
        dispatched = False
    return (DISPATCHED if dispatched else DISPATCH_FAILED), detail


def request_train_full(gh_runner, root, github_get, *, profile=_UNSET):
    """Merge-train exception: no quiet period, same dedupe and SHA checks; return a code."""
    return request_full(gh_runner, root, github_get, quiet_seconds=0, profile=profile)[0]


def add_ci_parser(sub):
    ci = sub.add_parser('ci', help='Deterministic CI helpers')
    actions = ci.add_subparsers(dest='action', required=True)
    actions.add_parser('full-mac-due', help='Dispatch the main FULL Mac suite only when due')
    from .main_incident_cli import add_main_incident_parser  # #1706 R3
    add_main_incident_parser(actions)
    return ci


def run_ci(args, *, gh_runner=None, github_get=None, root=None, now=None, profile=_UNSET):
    """Print one JSON line; exit 0 normal, 1 failed FULL or alert, 2 read or dispatch error."""
    if github_get is None:
        from .task_completion import _github_get as github_get
    if gh_runner is None:
        from .task_advance import _publish_run_gh as gh_runner
    code, detail = request_full(gh_runner, os.getcwd() if root is None else root,
                                github_get, now=now, profile=profile)
    print(json.dumps(dict(detail, code=code), sort_keys=True))
    if code in (UNAVAILABLE, DISPATCH_FAILED, PROFILE_INVALID):
        print(f'llmtool: {code}', file=sys.stderr)
        return 2
    return 0 if code in OK_CODES and 'alert' not in detail else 1
