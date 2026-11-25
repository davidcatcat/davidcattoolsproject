"""Main CI job fetch (#2602 slice 3): bounded read of every failed main job.

``llmtool ci main-incident jobs`` must report every failed job, required or not,
for the current main SHA's latest Linux push run and latest FULL Mac run. This
module performs that bounded, read-only read through the same injectable
``github_get(path)`` (relative to the repository API) that ``main_incident_cli``
uses. Nothing here calls the network directly, writes GitHub, or touches the
incident store.

Read plan:

* ``branches/main``: the current main SHA.
* ``actions/workflows/<workflow>/runs?branch=main&event=<event>&per_page=100``:
  the newest runs of the Linux push workflow and the FULL Mac workflow.
* ``actions/runs/<id>/jobs?per_page=100&page=<n>``: the jobs of the selected
  run, at most ``MAX_JOB_PAGES`` pages.

Every bound is explicit. A full run listing with no matching SHA, a job count
that cannot be read within ``MAX_JOB_PAGES``, or any failed read leaves that
run UNKNOWN; it is never treated as a clean result. The selected run is the
newest (highest id) run for the main SHA, whatever its state, so an older
success cannot hide a newer failure or a pending rerun (the one exception is
the completed skip-only Mac dispatch described below).

Each run's pages are passed through ``main_ci_job_pages.collect_pages`` (SHA,
run id and run attempt all bound) and then ``main_ci_job_summary.summarize``.
Totals and pages stay per run; they are never pooled across runs before
validation. Even when the listing is UNKNOWN, the summary's known failing jobs
are still reported with their run identity.

A Mac ``workflow_dispatch`` run counts as the FULL run only when its listing
shows the FULL shard jobs actually ran. The full-due gate can dispatch a run
that skips the shards (``select`` and ``check`` succeed, ``shards`` are
``skipped``); such a run did not execute the FULL suite, so a completed
skip-only run is never GREEN and the newest older run that did run the shards
is selected instead. The scan of older matching runs is bounded by
``MAX_RUNS_TO_SCAN``; exhausting it, or the run listing, is UNKNOWN.

The result is a new dict: ``sha``, ``state`` (``red``, ``green`` or
``unknown``), ``reason`` (the first UNKNOWN reason, else None), ``runs`` (one
entry per relevant run with ``kind``, ``workflow``, ``run_id``, ``run_attempt``,
``state``, ``reason`` and ``failing``) and ``failing`` (every known failing job
with its run identity). The overall state is GREEN only when both runs are
complete, successful and well formed with no failing job; any missing, pending,
malformed, mismatched or failed read keeps it UNKNOWN.
"""

from __future__ import annotations

from . import mac_full_due
from . import main_ci_job_pages as pages_mod
from . import main_ci_job_summary as summary_mod
from . import main_incident as mi

RED = summary_mod.RED
GREEN = summary_mod.GREEN
UNKNOWN = summary_mod.UNKNOWN

LINUX = 'linux'
FULL_MAC = 'full_mac'

MAIN = 'branches/main'
LINUX_WORKFLOW = 'llmtool-linux-ci.yml'
MAC_WORKFLOW = 'llmtool-mac-ci.yml'
RUNS = ('actions/workflows/{workflow}/runs?branch=main&event={event}'
        '&per_page={per_page}')
JOBS = 'actions/runs/{run_id}/jobs?per_page={per_page}&page={page}'

RUNS_PER_PAGE = 100
JOBS_PER_PAGE = 100
MAX_JOB_PAGES = 10
MAX_RUNS_TO_SCAN = 10

# The FULL shard jobs prove the Mac dispatch actually executed the FULL suite;
# ``check`` runs in a skip-only dispatch too, so it is not evidence.
FULL_SHARDS = mac_full_due.JOBS[:3]

# Short reason codes naming why a run or the whole report is UNKNOWN.
MAIN_READ_FAILED = 'main_read_failed'
RUN_LIST_FAILED = 'run_list_failed'
RUN_LIST_MALFORMED = 'run_list_malformed'
RUN_LIST_BOUND = 'run_list_bound'
RUN_MISSING = 'run_missing'
RUN_INVALID = 'run_invalid'
RUN_PENDING = 'run_pending'
RUN_FAILED = 'run_failed'
RUN_CONCLUSION_UNKNOWN = 'run_conclusion_unknown'
PAGES_FAILED = 'pages_failed'
PAGES_MALFORMED = 'pages_malformed'
PAGES_BOUND = 'pages_bound'
FULL_EVIDENCE_MISSING = 'full_evidence_missing'
RUN_SCAN_BOUND = 'run_scan_bound'

# One entry per relevant check: kind, workflow path and the run event that
# selects exactly the Linux push run and the FULL Mac (workflow_dispatch) run.
_KINDS = (
    (LINUX, LINUX_WORKFLOW, 'push'),
    (FULL_MAC, MAC_WORKFLOW, 'workflow_dispatch'),
)


def _read(github_get, path):
    try:
        data = github_get(path)
    except Exception:
        raise mac_full_due.StateError(path) from None
    if not isinstance(data, (dict, list)):
        raise mac_full_due.StateError(path)
    return data


def main_sha(github_get):
    """Return the current main commit SHA; raise StateError on any bad read."""
    data = _read(github_get, MAIN)
    commit = data.get('commit') if isinstance(data, dict) else None
    sha = commit.get('sha') if isinstance(commit, dict) else None
    if not isinstance(sha, str) or mac_full_due.SHA.match(sha) is None:
        raise mac_full_due.StateError(MAIN)
    return sha


def _listed_runs(github_get, workflow, event):
    """Return (runs, None) from one bounded listing, or (None, reason)."""
    path = RUNS.format(workflow=workflow, event=event, per_page=RUNS_PER_PAGE)
    try:
        data = _read(github_get, path)
    except mac_full_due.StateError:
        return None, RUN_LIST_FAILED
    runs = data.get('workflow_runs') if isinstance(data, dict) else None
    if not isinstance(runs, list) or not all(isinstance(run, dict) for run in runs):
        return None, RUN_LIST_MALFORMED
    return runs, None


def _matching_runs(runs, sha):
    """Return (matching runs newest first, None), or (None, reason).

    The order is chosen before any status is read, so a newer pending or
    failing run is always considered before an older success.
    """
    matching = [run for run in runs if run.get('head_sha') == sha]
    if not matching:
        return None, RUN_MISSING
    if any(type(run.get('id')) is not int or run['id'] <= 0 for run in matching):
        return None, RUN_INVALID
    ids = [run['id'] for run in matching]
    if len(set(ids)) != len(ids):
        return None, RUN_INVALID
    return sorted(matching, key=lambda item: item['id'], reverse=True), None


def _full_evidence(pages):
    """True when the FULL shard jobs are present and none of them was skipped."""
    conclusions = {}
    for jobs in pages:
        for job in jobs:
            if isinstance(job, dict):
                conclusions.setdefault(job.get('name'), job.get('conclusion'))
    return all(conclusions.get(name) not in (None, 'skipped') for name in FULL_SHARDS)


def _run_guard(run):
    """Return None for a completed successful run, else the UNKNOWN reason."""
    status, conclusion = run.get('status'), run.get('conclusion')
    if not isinstance(status, str) or not status.strip():
        return RUN_INVALID
    if status != 'completed':
        return RUN_PENDING
    if not isinstance(conclusion, str) or not conclusion.strip():
        return RUN_CONCLUSION_UNKNOWN
    if conclusion in mi.BAD:
        return RUN_FAILED
    if conclusion != 'success':
        return RUN_CONCLUSION_UNKNOWN
    return None


def _job_pages(github_get, run_id):
    """Return (pages read so far, None) or (pages, reason); never over-fetch."""
    pages = []
    stated_total = None
    for page in range(1, MAX_JOB_PAGES + 1):
        path = JOBS.format(run_id=run_id, per_page=JOBS_PER_PAGE, page=page)
        try:
            data = _read(github_get, path)
        except mac_full_due.StateError:
            return pages, PAGES_FAILED
        total = data.get('total_count') if isinstance(data, dict) else None
        jobs = data.get('jobs') if isinstance(data, dict) else None
        if type(total) is not int or total < 0 or not isinstance(jobs, list):
            # Keep a usable job list so a known failure on a page with a bad
            # total_count is still reported; the listing stays UNKNOWN.
            if isinstance(data, dict) and isinstance(jobs, list):
                pages.append(data)
            return pages, PAGES_MALFORMED
        if stated_total is None:
            stated_total = total
            pages.append(data)
            if total > MAX_JOB_PAGES * JOBS_PER_PAGE:
                return pages, PAGES_BOUND
        else:
            pages.append(data)
            if total != stated_total:
                # Stop here; the pages validator names the total mismatch.
                return pages, None
        received = sum(len(item.get('jobs', [])) for item in pages
                       if isinstance(item, dict) and isinstance(item.get('jobs'), list))
        if received >= stated_total:
            return pages, None
    return pages, PAGES_BOUND


def _job_lists(pages):
    """Best-effort job lists for failure evidence, ignoring unusable pages."""
    lists = []
    for page in pages:
        jobs = page.get('jobs') if isinstance(page, dict) else None
        if isinstance(jobs, list):
            lists.append(jobs)
    return lists


def _job_identity(pages):
    """Map each job id to the run identity its own job data carries."""
    identity = {}
    for jobs in _job_lists(pages):
        for job in jobs:
            if not isinstance(job, dict) or type(job.get('id')) is not int:
                continue
            run_id, run_attempt = job.get('run_id'), job.get('run_attempt')
            identity.setdefault(job['id'], (
                run_id if type(run_id) is int else None,
                run_attempt if type(run_attempt) is int else None))
    return identity


def _failing_with_identity(failing, pages, run_id, run_attempt):
    """Label each failing job with its own identity, never another run's.

    A job taken from mismatched pages keeps the run id and attempt its job
    data carries; only a job whose own identity is missing falls back to the
    selected run.
    """
    identity = _job_identity(pages)
    labelled = []
    for item in failing:
        actual_run, actual_attempt = identity.get(
            item.get('id'), (run_id, run_attempt))
        labelled.append(dict(item, run_id=actual_run, run_attempt=actual_attempt))
    return labelled


def _run_report(github_get, sha, kind, workflow, event, run):
    """Return (one run's report, True when it is a completed skip-only Mac run)."""
    result = {'kind': kind, 'workflow': workflow, 'run_id': run['id'],
              'run_attempt': run['run_attempt'], 'state': UNKNOWN,
              'reason': None, 'failing': []}
    run_id, run_attempt = run['id'], run['run_attempt']
    guard = _run_guard(run)
    pages, reason = _job_pages(github_get, run_id)
    if reason is not None:
        result['reason'] = reason
        result['failing'] = _failing_with_identity(
            summary_mod.summarize(sha, _job_lists(pages), None)['failing'],
            pages, run_id, run_attempt)
        return result, False
    collected = pages_mod.collect_pages(sha, run_id, run_attempt, pages)
    if collected['state'] != pages_mod.OK:
        result['reason'] = collected['reason']
        # The validator rejects the listing as a whole, but any well-formed
        # failing job in the fetched pages is still reported.
        result['failing'] = _failing_with_identity(
            summary_mod.summarize(
                sha, _job_lists(pages), collected['stated_total'])['failing'],
            pages, run_id, run_attempt)
        return result, False
    summary = summary_mod.summarize(
        sha, collected['pages'], collected['stated_total'])
    result['failing'] = _failing_with_identity(
        summary['failing'], pages, run_id, run_attempt)
    if guard in (RUN_PENDING, RUN_CONCLUSION_UNKNOWN, RUN_INVALID):
        # The run itself is not a completed success, so no job listing can
        # make the report GREEN; known failing jobs are still reported.
        result['reason'] = guard
    elif summary['state'] == RED:
        result['state'] = RED
    elif guard == RUN_FAILED:
        result['reason'] = RUN_FAILED
    else:
        result['state'] = summary['state']
        result['reason'] = summary['reason']
        if (kind == FULL_MAC and result['state'] == GREEN
                and not _full_evidence(collected['pages'])):
            # A success that skipped the FULL shards did not run the suite.
            return result, True
    return result, False


def _run_result(github_get, sha, kind, workflow, event):
    """Return one run's bounded report; never GREEN on missing data."""
    result = {'kind': kind, 'workflow': workflow, 'run_id': None,
              'run_attempt': None, 'state': UNKNOWN, 'reason': None, 'failing': []}
    runs, reason = _listed_runs(github_get, workflow, event)
    if runs is None:
        result['reason'] = reason
        return result
    matching, reason = _matching_runs(runs, sha)
    if matching is None:
        if reason == RUN_MISSING and len(runs) >= RUNS_PER_PAGE:
            reason = RUN_LIST_BOUND
        result['reason'] = reason
        return result
    first_skip = None
    for run in matching[:MAX_RUNS_TO_SCAN]:
        attempt = run.get('run_attempt')
        if type(attempt) is not int or attempt <= 0:
            result['run_id'] = run['id']
            result['reason'] = RUN_INVALID
            return result
        candidate, skip = _run_report(github_get, sha, kind, workflow, event, run)
        if not skip:
            return candidate
        if first_skip is None:
            first_skip = candidate
    if first_skip is None:  # only when MAX_RUNS_TO_SCAN is zero
        result['reason'] = RUN_INVALID
        return result
    # Every examined run was a completed skip-only Mac dispatch. Never GREEN:
    # name the bound if an older FULL run may still be outside it.
    first_skip['state'] = UNKNOWN
    if len(matching) > MAX_RUNS_TO_SCAN:
        first_skip['reason'] = RUN_SCAN_BOUND
    elif len(runs) >= RUNS_PER_PAGE:
        first_skip['reason'] = RUN_LIST_BOUND
    else:
        first_skip['reason'] = FULL_EVIDENCE_MISSING
    return first_skip


def inspect_jobs(github_get):
    """Return the bounded, read-only failing-job report for current main."""
    result = {'sha': None, 'state': UNKNOWN, 'reason': None,
              'runs': [], 'failing': []}
    try:
        sha = main_sha(github_get)
    except mac_full_due.StateError:
        result['reason'] = MAIN_READ_FAILED
        return result
    result['sha'] = sha
    runs = [_run_result(github_get, sha, kind, workflow, event)
            for kind, workflow, event in _KINDS]
    result['runs'] = runs
    result['failing'] = [
        dict(job, kind=run['kind'], workflow=run['workflow'])
        for run in runs for job in run['failing']]
    unknown = [run for run in runs if run['state'] == UNKNOWN]
    if unknown:
        result['state'] = UNKNOWN
        result['reason'] = unknown[0]['reason']
    elif any(run['state'] == RED for run in runs):
        result['state'] = RED
    else:
        result['state'] = GREEN
    return result
