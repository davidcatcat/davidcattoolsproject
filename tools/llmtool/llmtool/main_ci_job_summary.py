"""Main CI job summary (#2125 R1): a non-required red job must not stay red unnoticed.

A main CI run stays red for over a day because only required checks are watched:
the non-required ``gateway tests (Linux 3.14)`` job failed and nothing reported
it. This pure module summarizes the job listing the caller already fetched for
one main commit SHA: it lists every completed job whose conclusion is failing,
required or not, and reports RED, GREEN or UNKNOWN for the whole SHA.

The failing conclusions are exactly ``main_incident.BAD`` (``failure``,
``cancelled``, ``timed_out``, ``startup_failure``); the set is reused, not
copied.

Pure and read-only: nothing here calls GitHub, the network or Git, reads a
file, or mutates the input. Inputs:

* ``sha``: the expected main commit SHA (40 hex).
* ``pages``: the job listing as the caller fetched it from the GitHub Actions
  jobs listings, one or more pages; each page is a list of job dicts, each job
  with at least ``id``, ``name``, ``head_sha``, ``status`` and ``conclusion``.
* ``stated_total``: the total job count for the SHA as reported by GitHub.

The result is a new dict: ``state`` is RED when the listing is complete and
well formed and at least one job is failing, GREEN when it is complete and
well formed and none is failing, UNKNOWN otherwise, and ``reason`` names the
first UNKNOWN reason. A result never claims GREEN on incomplete, still running
or malformed data; failing jobs found are still reported on an UNKNOWN result.
"""

from __future__ import annotations

import re

from . import main_incident

RED = 'red'
GREEN = 'green'
UNKNOWN = 'unknown'

# Short reason codes naming why the result is UNKNOWN.
SHA_INVALID = 'sha_invalid'
PAGES_MALFORMED = 'pages_malformed'
STATED_TOTAL_INVALID = 'stated_total_invalid'
SHA_MISMATCH = 'sha_mismatch'
COUNT_MISMATCH = 'count_mismatch'
DUPLICATE_JOB_ID = 'duplicate_job_id'
MALFORMED_JOB = 'malformed_job'
COMPLETED_WITHOUT_CONCLUSION = 'completed_without_conclusion'
JOB_NOT_COMPLETED = 'job_not_completed'

_SHA = re.compile(r'^[0-9a-f]{40}$')
_COMPLETED = 'completed'
_FAILED = frozenset(main_incident.BAD)


def summarize(sha, pages, stated_total):
    """Summarize the jobs of one main commit SHA; return a new dict.

    ``failing`` lists every job that completed with a conclusion in
    ``main_incident.BAD`` (required and non-required alike), each with its
    ``name``, ``conclusion`` and ``id``, in the input order of the pages.
    ``state`` is
    ``red``, ``green`` or ``unknown``; ``reason`` names the first UNKNOWN
    reason, and is None on RED and GREEN.
    """
    result = {'sha': sha, 'state': UNKNOWN, 'reason': None,
              'stated_total': stated_total, 'total_jobs': 0, 'failing': []}
    if not isinstance(sha, str) or _SHA.match(sha) is None:
        return _unknown(result, SHA_INVALID)
    if not isinstance(pages, (list, tuple)) or any(
            not isinstance(page, (list, tuple)) for page in pages):
        return _unknown(result, PAGES_MALFORMED)
    if type(stated_total) is not int:
        return _unknown(result, STATED_TOTAL_INVALID)
    # Scan the whole listing first: every completed failing job found must be
    # reported even when the result ends up UNKNOWN.
    failing = []
    seen_ids = set()
    duplicate_id = False
    not_completed = False
    count = 0
    for page in pages:
        for job in page:
            valid, reason = _valid_job(job, sha)
            if not valid:
                return _unknown(result, reason, failing)
            count += 1
            seen = seen_ids.add(job['id'])
            if seen:
                duplicate_id = True
            if job['status'] != _COMPLETED:
                not_completed = True
            elif job['conclusion'] in _FAILED:
                _add_failing(failing, job)
    result['total_jobs'] = count
    if not count or count != stated_total:
        # Empty, missing pages, extra jobs or duplicate ids cannot establish
        # a complete, well-formed listing.
        return _unknown(result, DUPLICATE_JOB_ID if duplicate_id else COUNT_MISMATCH,
                        failing)
    if not_completed:
        return _unknown(result, JOB_NOT_COMPLETED, failing)
    if failing:
        return _with_failing(result, RED, None, failing)
    return _with_failing(result, GREEN, None, failing)


def _unknown(result, reason, failing=None):
    return _with_failing(result, UNKNOWN, reason, failing or [])


def _with_failing(result, state, reason, failing):
    result.update({'state': state, 'reason': reason, 'failing': failing})
    return result


def _valid_job(job, sha):
    if not isinstance(job, dict):
        return False, MALFORMED_JOB
    if type(job.get('id')) is not int:
        return False, MALFORMED_JOB
    if not isinstance(job.get('name'), str) or not isinstance(job.get('status'), str):
        return False, MALFORMED_JOB
    if job.get('head_sha') != sha:
        return False, SHA_MISMATCH
    conclusion = job.get('conclusion')
    if conclusion is None:
        if job['status'] == _COMPLETED:
            return False, COMPLETED_WITHOUT_CONCLUSION
        return True, None
    return isinstance(conclusion, str), MALFORMED_JOB


def _add_failing(failing, job):
    ids = {item['id'] for item in failing}
    if job['id'] not in ids:
        failing.append({'id': job['id'], 'name': job['name'],
                        'conclusion': job['conclusion']})
