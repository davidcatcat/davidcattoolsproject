"""Request workflow_dispatch run of the Mac full suite on main."""

from __future__ import annotations

MAC_FULL_SUITE_DISPATCHED = 'MAC_FULL_SUITE_DISPATCHED'
MAC_FULL_SUITE_ALREADY_QUEUED = 'MAC_FULL_SUITE_ALREADY_QUEUED'
MAC_FULL_SUITE_DISPATCH_FAILED = 'MAC_FULL_SUITE_DISPATCH_FAILED'
MAC_FULL_SUITE_STATE_UNAVAILABLE = 'MAC_FULL_SUITE_STATE_UNAVAILABLE'

_QUEUED_STATUSES = frozenset({'queued', 'waiting', 'pending', 'requested'})
_WORKFLOW_FILE = 'llmtool-mac-ci.yml'
_WORKFLOW_RUNS_PATH = (
    'actions/workflows/llmtool-mac-ci.yml/runs?branch=main&event=workflow_dispatch&per_page=20'
)


def request_full_suite(gh_runner, root, github_get):
    """Request one workflow_dispatch run of llmtool-mac-ci.yml on main.

    Returns MAC_FULL_SUITE_ALREADY_QUEUED if a run is already queued or waiting,
    MAC_FULL_SUITE_DISPATCHED on exit 0, MAC_FULL_SUITE_DISPATCH_FAILED on runner
    exit non-zero or exception, or MAC_FULL_SUITE_STATE_UNAVAILABLE if runner or
    github_get is missing, the read fails, or the run list is malformed.
    Never raises, never retries, and sends no other GitHub write.
    """
    try:
        if gh_runner is None or github_get is None:
            return MAC_FULL_SUITE_STATE_UNAVAILABLE

        try:
            data = github_get(_WORKFLOW_RUNS_PATH)
        except Exception:
            return MAC_FULL_SUITE_STATE_UNAVAILABLE

        if not isinstance(data, dict):
            return MAC_FULL_SUITE_STATE_UNAVAILABLE

        runs = data.get('workflow_runs')
        if not isinstance(runs, list):
            return MAC_FULL_SUITE_STATE_UNAVAILABLE

        for run in runs:
            if not isinstance(run, dict):
                return MAC_FULL_SUITE_STATE_UNAVAILABLE
            status = run.get('status')
            if not isinstance(status, str):
                return MAC_FULL_SUITE_STATE_UNAVAILABLE

        for run in runs:
            status = run['status']
            if status in _QUEUED_STATUSES or status.lower() in _QUEUED_STATUSES:
                return MAC_FULL_SUITE_ALREADY_QUEUED

        try:
            result = gh_runner(root, ['workflow', 'run', _WORKFLOW_FILE, '--ref', 'main'])
        except Exception:
            return MAC_FULL_SUITE_DISPATCH_FAILED

        try:
            returncode = getattr(result, 'returncode', result)
            if returncode == 0:
                return MAC_FULL_SUITE_DISPATCHED
            return MAC_FULL_SUITE_DISPATCH_FAILED
        except Exception:
            return MAC_FULL_SUITE_DISPATCH_FAILED
    except Exception:
        return MAC_FULL_SUITE_STATE_UNAVAILABLE
