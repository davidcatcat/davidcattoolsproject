"""Main CI job pages (#2602 slice 2): turn fetched job pages into summary input.

``main_ci_job_summary`` summarizes one main commit SHA from a list of job pages
and a stated total. The caller fetches those pages from the GitHub Actions jobs
API (``actions/runs/<id>/jobs``), but a page listing can be incomplete or mix
data from another run. This pure module checks the caller-supplied pages for
one workflow run and returns exactly the ``pages`` and ``stated_total`` that
``main_ci_job_summary.summarize`` expects, or an UNKNOWN result with a reason
when the listing cannot be trusted.

Pure and read-only: nothing here calls GitHub, the network or Git, reads a
file, or mutates the input. Inputs:

* ``sha``: the expected main commit SHA (40 hex).
* ``run_id``: the workflow run the pages belong to.
* ``run_attempt``: that run's attempt.
* ``pages``: the already-fetched jobs API responses, one per page; each is a
  dict with ``total_count`` and ``jobs``, and each job has at least ``id``,
  ``name``, ``head_sha``, ``status``, ``conclusion``, ``run_id`` and
  ``run_attempt``.

The result is a new dict. On success ``state`` is ``ok``, ``reason`` is None,
and ``pages`` is a list of job lists in the input page order with
``stated_total`` the agreed ``total_count``, so the caller can pass them
straight to ``main_ci_job_summary.summarize``. Otherwise ``state`` is
``unknown`` and ``reason`` names the first problem found; the result never
claims a usable listing on incomplete, inconsistent or malformed data.
"""

from __future__ import annotations

import re

OK = 'ok'
UNKNOWN = 'unknown'

# Short reason codes naming why the result is UNKNOWN.
SHA_INVALID = 'sha_invalid'
RUN_ID_INVALID = 'run_id_invalid'
RUN_ATTEMPT_INVALID = 'run_attempt_invalid'
PAGES_MALFORMED = 'pages_malformed'
MISSING_PAGE = 'missing_page'
TOTAL_COUNT_MISMATCH = 'total_count_mismatch'
DUPLICATE_PAGE = 'duplicate_page'
DUPLICATE_JOB_ID = 'duplicate_job_id'
SHA_MISMATCH = 'sha_mismatch'
RUN_ID_MISMATCH = 'run_id_mismatch'
RUN_ATTEMPT_MISMATCH = 'run_attempt_mismatch'
MALFORMED_JOB = 'malformed_job'

_SHA = re.compile(r'\A[0-9a-f]{40}\Z')


def collect_pages(sha, run_id, run_attempt, pages):
    """Return the summary input for one run, or UNKNOWN with a reason.

    ``state`` is ``ok`` only when every page is well formed, agrees on
    ``total_count``, covers that many jobs, has no duplicate page or job id,
    and every job matches ``sha``, ``run_id`` and ``run_attempt``.
    """
    result = {'state': UNKNOWN, 'reason': None, 'sha': sha, 'run_id': run_id,
              'run_attempt': run_attempt, 'pages': [], 'stated_total': None}
    if not isinstance(sha, str) or _SHA.match(sha) is None:
        return _unknown(result, SHA_INVALID)
    if type(run_id) is not int or run_id <= 0:
        return _unknown(result, RUN_ID_INVALID)
    if type(run_attempt) is not int or run_attempt <= 0:
        return _unknown(result, RUN_ATTEMPT_INVALID)
    if not isinstance(pages, (list, tuple)):
        return _unknown(result, PAGES_MALFORMED)
    if not pages:
        return _unknown(result, MISSING_PAGE)
    stated_total = None
    totals_differ = False
    seen_pages = set()
    seen_ids = set()
    duplicate_page = False
    duplicate_job = False
    out_pages = []
    received = 0
    for page in pages:
        if not isinstance(page, dict):
            return _unknown(result, PAGES_MALFORMED)
        total = page.get('total_count')
        jobs = page.get('jobs')
        if type(total) is not int or total < 0 or not isinstance(jobs, (list, tuple)):
            return _unknown(result, PAGES_MALFORMED)
        if stated_total is None:
            stated_total = total
        elif total != stated_total:
            totals_differ = True
        page_jobs = []
        page_ids = []
        for job in jobs:
            reason = _job_reason(job, sha, run_id, run_attempt)
            if reason is not None:
                return _unknown(result, reason)
            page_ids.append(job['id'])
            page_jobs.append(dict(job))
        # Every page, empty ones included, takes part in duplicate detection: a
        # repeated empty page is just as ambiguous as a repeated non-empty one.
        signature = tuple(page_ids)
        if signature in seen_pages:
            duplicate_page = True
        seen_pages.add(signature)
        for job_id in page_ids:
            if job_id in seen_ids:
                duplicate_job = True
            seen_ids.add(job_id)
        received += len(page_ids)
        out_pages.append(page_jobs)
    result['pages'] = out_pages
    result['stated_total'] = stated_total
    if duplicate_page:
        return _unknown(result, DUPLICATE_PAGE)
    if duplicate_job:
        return _unknown(result, DUPLICATE_JOB_ID)
    if totals_differ:
        return _unknown(result, TOTAL_COUNT_MISMATCH)
    if received != stated_total:
        reason = MISSING_PAGE if received < stated_total else TOTAL_COUNT_MISMATCH
        return _unknown(result, reason)
    result['state'] = OK
    return result


def _unknown(result, reason):
    result['reason'] = reason
    return result


def _job_reason(job, sha, run_id, run_attempt):
    if not isinstance(job, dict):
        return MALFORMED_JOB
    if type(job.get('id')) is not int or job.get('id') <= 0:
        return MALFORMED_JOB
    name, status = job.get('name'), job.get('status')
    if not isinstance(name, str) or not name.strip():
        return MALFORMED_JOB
    if not isinstance(status, str) or not status.strip():
        return MALFORMED_JOB
    conclusion = job.get('conclusion')
    if conclusion is not None and (not isinstance(conclusion, str) or not conclusion.strip()):
        return MALFORMED_JOB
    if type(job.get('run_id')) is not int:
        return MALFORMED_JOB
    if type(job.get('run_attempt')) is not int:
        return MALFORMED_JOB
    if job.get('head_sha') != sha:
        return SHA_MISMATCH
    if job['run_id'] != run_id:
        return RUN_ID_MISMATCH
    if job['run_attempt'] != run_attempt:
        return RUN_ATTEMPT_MISMATCH
    return None
