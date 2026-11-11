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
