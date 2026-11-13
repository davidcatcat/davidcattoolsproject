"""Main FULL Mac suite trigger for ``llmtool ci full-mac-due`` (Owner #1485 P1).

One FULL is dispatched only when all four Owner conditions hold: main has a SHA
FULL has not verified, the last main merge (GitHub merge time) is at least
QUIET_SECONDS old, no main FULL is running or queued, and the main SHA has no
trusted FULL success. Every value is read from GitHub, so a pause loses nothing.
A FULL run tests its own head_sha (the workflow checks out github.sha).
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
import time

QUIET_SECONDS = 1800
RUNS = 'actions/workflows/llmtool-mac-ci.yml/runs?branch=main&event=workflow_dispatch&per_page=50'
JOBS = ('shards (1/3)', 'shards (2/3)', 'shards (3/3)', 'check (macOS 3.13)')
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
OK_CODES = (DISPATCHED, ACTIVE_RUN, VERIFIED, QUIET)


class StateError(Exception):
    """A GitHub read failed or returned an unexpected shape."""


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


def full_runs(github_get):
    """Return main workflow_dispatch runs of the Mac workflow, newest first."""
    data = _get(github_get, RUNS)
    runs = data.get('workflow_runs') if isinstance(data, dict) else None
    if not isinstance(runs, list) or not all(
            isinstance(run, dict) and isinstance(run.get('status'), str) for run in runs):
        raise StateError(RUNS)
    return runs


def trusted(github_get, run):
    """A success counts only when every shard job and the check job succeeded."""
    if run.get('status') != 'completed' or run.get('conclusion') != 'success':
        return False
    run_id = run.get('id')
    if type(run_id) is not int:
        raise StateError('run id')
    data = _get(github_get, f'actions/runs/{run_id}/jobs?per_page=100')
    jobs = data.get('jobs') if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        raise StateError('jobs')
    done = {job.get('name'): job.get('conclusion') for job in jobs if isinstance(job, dict)}
    return all(done.get(name) == 'success' for name in JOBS)


def sha_result(github_get, runs, sha, skip_id=None):
    """Return PASS, FAIL or None from the newest finished FULL run of sha."""
    for run in runs:
        if run.get('id') == skip_id or run.get('head_sha') != sha or run['status'] != 'completed':
            continue
        if trusted(github_get, run):
            return 'PASS'
        if run.get('conclusion') in FAILED:
            return 'FAIL'
    return None


def decide(github_get, *, now, quiet_seconds=QUIET_SECONDS):
    """Return (code, detail) from GitHub state; never writes."""
    sha, merged_at = main_state(github_get)
    runs = full_runs(github_get)
    detail = {'main_sha': sha, 'merge_age_seconds': int(now - merged_at)}
    finished = [run for run in runs if run['status'] == 'completed' and
                run.get('conclusion') in FAILED | {'success'}]
    if finished and finished[0].get('conclusion') in FAILED:
        detail['alert'] = {'sha': finished[0].get('head_sha'), 'run': finished[0].get('html_url'),
                           'conclusion': finished[0].get('conclusion')}
    active = [run.get('id') for run in runs if run['status'].lower() in ACTIVE]
    if active:
        return ACTIVE_RUN, dict(detail, active_runs=active)
    result = sha_result(github_get, runs, sha)
    if result == 'PASS':
        return VERIFIED, detail
    if result == 'FAIL':
        return FULL_FAILED, detail
    if quiet_seconds > 0 and now - merged_at < quiet_seconds:
        return QUIET, detail
    return DUE, detail


def request_full(gh_runner, root, github_get, *, now=None, quiet_seconds=QUIET_SECONDS):
    """Dispatch one FULL on main when due; return (code, detail)."""
    now = time.time() if now is None else now
    try:
        code, detail = decide(github_get, now=now, quiet_seconds=quiet_seconds)
        if code != DUE:
            return code, detail
        # Re-read just before dispatch so a run from an overlapping sweep is seen.
        if any(run['status'].lower() in ACTIVE for run in full_runs(github_get)):
            return ACTIVE_RUN, detail
    except StateError as exc:
        return UNAVAILABLE, {'read': str(exc)}
    try:
        result = gh_runner(root, ['workflow', 'run', 'llmtool-mac-ci.yml', '--ref', 'main'])
        dispatched = getattr(result, 'returncode', 1) == 0
    except Exception:
        dispatched = False
    return (DISPATCHED if dispatched else DISPATCH_FAILED), detail


def request_train_full(gh_runner, root, github_get):
    """Merge-train exception: no quiet period, same dedupe and SHA checks; return a code."""
    return request_full(gh_runner, root, github_get, quiet_seconds=0)[0]


def add_ci_parser(sub):
    ci = sub.add_parser('ci', help='Deterministic CI helpers')
    actions = ci.add_subparsers(dest='action', required=True)
    actions.add_parser('full-mac-due', help='Dispatch the main FULL Mac suite only when due')
    from .main_incident_cli import add_main_incident_parser  # #1706 R3
    add_main_incident_parser(actions)
    return ci


def run_ci(args, *, gh_runner=None, github_get=None, root=None, now=None):
    """Print one JSON line; exit 0 normal, 1 failed FULL or alert, 2 read or dispatch error."""
    if github_get is None:
        from .task_completion import _github_get as github_get
    if gh_runner is None:
        from .task_advance import _publish_run_gh as gh_runner
    code, detail = request_full(gh_runner, os.getcwd() if root is None else root,
                                github_get, now=now)
    print(json.dumps(dict(detail, code=code), sort_keys=True))
    if code in (UNAVAILABLE, DISPATCH_FAILED):
        print(f'llmtool: {code}', file=sys.stderr)
        return 2
    return 0 if code in OK_CODES and 'alert' not in detail else 1
