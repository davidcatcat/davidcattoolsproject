"""Request one full-suite llmtool Mac CI run on main after a merge.

``task advance --merge`` and the merge train call request_full_suite once
after a merge. It reads recent workflow_dispatch runs of the Mac CI workflow
on main. If one is still waiting to start, it does nothing: that run checks
out the newest main when it starts. Otherwise it runs
``gh workflow run llmtool-mac-ci.yml --ref main`` once. A run already in
progress does not stop the request; the workflow's full-main concurrency
group holds the new run until the old one ends.

It returns one code and never raises. It never retries, never cancels a run
and sends no other GitHub write. A failed or skipped request never changes a
merge result; the caller reports the code.
"""

WORKFLOW_FILE = 'llmtool-mac-ci.yml'
RUNS_PER_PAGE = 30
RUNS_PATH = (f'actions/workflows/{WORKFLOW_FILE}/runs'
             f'?branch=main&event=workflow_dispatch&per_page={RUNS_PER_PAGE}')
DISPATCH_ARGS = ('workflow', 'run', WORKFLOW_FILE, '--ref', 'main')

MAC_FULL_SUITE_DISPATCHED = 'MAC_FULL_SUITE_DISPATCHED'
MAC_FULL_SUITE_ALREADY_QUEUED = 'MAC_FULL_SUITE_ALREADY_QUEUED'
MAC_FULL_SUITE_DISPATCH_FAILED = 'MAC_FULL_SUITE_DISPATCH_FAILED'
MAC_FULL_SUITE_STATE_UNAVAILABLE = 'MAC_FULL_SUITE_STATE_UNAVAILABLE'

# Codes that need no report: a run was requested or one already waits.
OK_CODES = frozenset((MAC_FULL_SUITE_DISPATCHED, MAC_FULL_SUITE_ALREADY_QUEUED))

# Run states that have not started yet. A new request would only replace
# this waiting run in the concurrency group, so none is sent.
_WAITING = frozenset(('queued', 'waiting', 'pending', 'requested'))
_STARTED = frozenset(('in_progress', 'completed'))


def _waiting_run(runs):
    """Return True/False for a waiting run, or None when the list is not usable."""
    if type(runs) is not dict:
        return None
    items = runs.get('workflow_runs')
    total = runs.get('total_count')
    if (type(items) is not list or type(total) is not int or
            total < len(items) or len(items) > RUNS_PER_PAGE):
        return None
    waiting = False
    for run in items:
        if type(run) is not dict:
            return None
        status = run.get('status')
        # The read filters on these; anything else means the list is not
        # what was asked for, so do not trust it.
        if run.get('event') != 'workflow_dispatch' or run.get('head_branch') != 'main':
            return None
        if status in _WAITING:
            waiting = True
        elif status not in _STARTED:
            return None
    return waiting


def _request(gh_runner, root, github_get):
    if not callable(gh_runner) or not callable(github_get):
        return MAC_FULL_SUITE_STATE_UNAVAILABLE
    try:
        runs = github_get(RUNS_PATH)
    except Exception:
        return MAC_FULL_SUITE_STATE_UNAVAILABLE
    waiting = _waiting_run(runs)
    if waiting is None:
        return MAC_FULL_SUITE_STATE_UNAVAILABLE
    if waiting:
        return MAC_FULL_SUITE_ALREADY_QUEUED
    try:
        result = gh_runner(root, list(DISPATCH_ARGS))
    except Exception:
        return MAC_FULL_SUITE_DISPATCH_FAILED
    if getattr(result, 'returncode', 1) != 0:
        return MAC_FULL_SUITE_DISPATCH_FAILED
    return MAC_FULL_SUITE_DISPATCHED


def request_full_suite(*, gh_runner, root, github_get):
    """Request one full Mac CI run on main; return one code and never raise.

    gh_runner(root, args) and github_get(path) have the same shapes that
    task_advance uses. A missing runner or read returns
    MAC_FULL_SUITE_STATE_UNAVAILABLE.
    """
    try:
        return _request(gh_runner, root, github_get)
    except Exception:
        return MAC_FULL_SUITE_STATE_UNAVAILABLE
