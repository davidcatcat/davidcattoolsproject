"""Request workflow_dispatch run of the Mac full suite on main.

Rule: queued-like status -> ALREADY_QUEUED; only in_progress or none -> dispatch one,
which becomes the single pending run. Status compare stays case-insensitive.
"""

from __future__ import annotations

from .mac_full_due import ProfileError, _DEFAULT_PROFILE, _UNSET, resolve_profile

MAC_FULL_SUITE_DISPATCHED = 'MAC_FULL_SUITE_DISPATCHED'
MAC_FULL_SUITE_ALREADY_QUEUED = 'MAC_FULL_SUITE_ALREADY_QUEUED'
MAC_FULL_SUITE_DISPATCH_FAILED = 'MAC_FULL_SUITE_DISPATCH_FAILED'
MAC_FULL_SUITE_STATE_UNAVAILABLE = 'MAC_FULL_SUITE_STATE_UNAVAILABLE'
MAC_FULL_SUITE_PROFILE_INVALID = 'MAC_FULL_SUITE_PROFILE_INVALID'

_QUEUED_STATUSES = frozenset({'queued', 'waiting', 'pending', 'requested'})
_WORKFLOW_FILE = _DEFAULT_PROFILE.macos_workflow_file
_WORKFLOW_RUNS_PATH = (
    f'actions/workflows/{_WORKFLOW_FILE}/runs?branch=main&event=workflow_dispatch&per_page=20'
)


def request_full_suite(gh_runner, root, github_get, *, profile=_UNSET):
    """Request one workflow_dispatch run of the configured Mac workflow on main.

    Rule: queued-like status -> ALREADY_QUEUED; only in_progress or none ->
    dispatch one, which becomes the single pending run. Status compare stays
    case-insensitive.

    Returns MAC_FULL_SUITE_PROFILE_INVALID if the CI profile is missing,
    invalid or ambiguous; MAC_FULL_SUITE_ALREADY_QUEUED if a run is already
    queued or waiting; MAC_FULL_SUITE_DISPATCHED on exit 0;
    MAC_FULL_SUITE_DISPATCH_FAILED on runner exit non-zero or exception; or
    MAC_FULL_SUITE_STATE_UNAVAILABLE if runner or github_get is missing, the
    read fails, or the run list is malformed.
    Never raises, never retries, and sends no other GitHub write.
    """
    try:
        try:
            resolved = resolve_profile(profile)
        except ProfileError:
            return MAC_FULL_SUITE_PROFILE_INVALID

        workflow_file = resolved.macos_workflow_file
        workflow_runs_path = (
            f'actions/workflows/{workflow_file}'
            '/runs?branch=main&event=workflow_dispatch&per_page=20'
        )

        if gh_runner is None or github_get is None:
            return MAC_FULL_SUITE_STATE_UNAVAILABLE

        try:
            data = github_get(workflow_runs_path)
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
            result = gh_runner(root, ['workflow', 'run', workflow_file, '--ref', 'main'])
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
