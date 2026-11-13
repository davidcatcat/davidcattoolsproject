"""Main CI incident classification (#1706 R1, #1019 Row 40).

Pure and read-only: every function takes already-fetched data and returns a new
value. Nothing here calls GitHub, the network or Git, and inputs are never
mutated. The result is advice for the PM; it never writes anywhere.

Inputs (newest first unless ``newest_first=False``; one flag orders both):

* ``runs``: one list of CI runs on main heads, ordered by recency. Each run is a
  dict with ``id`` (int), ``head_sha`` (40 hex), ``status`` and ``conclusion``.
  A required Linux check run has ``name`` exactly ``check (Linux 3.14)``; the
  docs check and every other name are ignored. A FULL Mac run is a
  ``workflow_dispatch`` run on ``head_branch`` ``main`` whose ``jobs`` list
  names every ``mac_full_due.JOBS`` job; a PR-scoped Mac run is never FULL.
  Optional run fields: ``failed_tests`` (list of test IDs parsed by the caller),
  ``failed_step`` or ``step_name`` (the step that failed) and ``reason`` (the
  cancel or failure reason text).
* ``commits``: main history, each a dict with ``sha``, optional ``pr`` (int)
  and optional ``changed`` (bool). ``changed`` is False only when the caller
  knows the commit cannot change test results; anything else is a change.

Classes: ENV needs positive environment evidence on every failing run and no
test failure; FLAKY needs a newer pass of the same check on a different SHA
with no change in between; CODE needs independent reproduction of one test
at the failing SHA (Linux and FULL Mac, or two FULL Mac runs). Anything else,
including a missing or unparsable test ID, is UNKNOWN.
"""

from __future__ import annotations

import re

from . import mac_full_due

LINUX_CHECK = 'check (Linux 3.14)'
LINUX = 'LINUX'
FULL_MAC = 'FULL_MAC'
KINDS = (LINUX, FULL_MAC)
ENV = 'ENV'
FLAKY = 'FLAKY'
CODE = 'CODE'
UNKNOWN = 'UNKNOWN'
ENV_CONCLUSIONS = frozenset({'cancelled', 'timed_out', 'startup_failure'})
BAD = ENV_CONCLUSIONS | {'failure'}
# A failed step is ENV evidence only when it is a known setup step, never a test step.
_SETUP_STEP = re.compile(r'checkout|set ?up|install|cache|download|upload|artifact|runner|dependenc',
                         re.IGNORECASE)
_TEST_TEXT = re.compile(r'test|shard|suite', re.IGNORECASE)
_ENV_REASON = re.compile(r'runner|capacity|lost communication|no space left|superseded|'
                         r'higher priority', re.IGNORECASE)


def _sha(value):
    return value if isinstance(value, str) and mac_full_due.SHA.match(value) else None


def _ordered(items, newest_first):
    """Return a new newest-first list; anything but a list or tuple is empty."""
    if not isinstance(items, (list, tuple)):
        return []
    items = list(items)
    return items if newest_first else items[::-1]


def run_kind(run):
    """Return LINUX, FULL_MAC or None for one run; never a name match on 'mac'."""
    if (not isinstance(run, dict) or type(run.get('id')) is not int or
            _sha(run.get('head_sha')) is None):
        return None
    if run.get('name') == LINUX_CHECK:
        return LINUX
    jobs = run.get('jobs')
    if (run.get('event') == 'workflow_dispatch' and run.get('head_branch') == 'main' and
            isinstance(jobs, list)):
        names = {job.get('name') for job in jobs if isinstance(job, dict)}
        if set(mac_full_due.JOBS) <= names:
            return FULL_MAC
    return None


def _completed(run):
    return run.get('status') == 'completed' and isinstance(run.get('conclusion'), str)


def _passed(kind, run):
    """Linux passes on success; FULL Mac only when every FULL job succeeded too."""
    if run.get('conclusion') != 'success':
        return False
    if kind == LINUX:
        return True
    done = {job.get('name'): job.get('conclusion') for job in run['jobs'] if isinstance(job, dict)}
    return all(done.get(name) == 'success' for name in mac_full_due.JOBS)


def _runs_on(runs, sha, newest_first):
    """Completed required-check runs of sha, newest first, as (kind, run)."""
    out = []
    for run in _ordered(runs, newest_first):
        kind = run_kind(run)
        if kind and run['head_sha'] == sha and _completed(run):
            out.append((kind, run))
    return out


def newest_runs(runs, sha, newest_first=True):
    """Return {kind: newest completed run} for sha."""
    newest = {}
    for kind, run in _runs_on(runs, sha, newest_first):
        newest.setdefault(kind, run)
    return newest


def trusted_pass(runs, sha, newest_first=True):
    """True only when the newest Linux check passed and the newest FULL Mac is trusted."""
    newest = newest_runs(runs, sha, newest_first)
    return all(kind in newest and _passed(kind, newest[kind]) for kind in KINDS)


def head_failed(runs, sha, newest_first=True):
    """True when the newest completed run of any required check on sha did not pass.

    A failed run followed by a green rerun on the same SHA is not failing.
    """
    return any(run['conclusion'] in BAD for run in newest_runs(runs, sha, newest_first).values())


def _commits(commits, newest_first):
    return [commit for commit in _ordered(commits, newest_first)
            if isinstance(commit, dict) and _sha(commit.get('sha'))]


def find_heads(commits, runs, newest_first=True):
    """Return (last trusted PASS sha, failing sha) walking main from newest.

    The failing head is the newest failed head newer than every trusted PASS;
    a trusted PASS newer than every failure means no open incident (fail None).
    """
    fail = None
    for commit in _commits(commits, newest_first):
        sha = commit['sha']
        if trusted_pass(runs, sha, newest_first):
            return sha, fail
        if fail is None and head_failed(runs, sha, newest_first):
            fail = sha
    return None, fail


def _entry(commit):
    pr = commit.get('pr')
    return {'sha': commit['sha'], 'pr': pr if type(pr) is int else None}


def commit_range(commits, pass_sha, fail_sha, newest_first=True):
    """Commits after pass_sha up to and including fail_sha, oldest first; None if unknown."""
    chain = _commits(commits, newest_first)[::-1]
    shas = [commit['sha'] for commit in chain]
    if pass_sha not in shas or fail_sha not in shas:
        return None
    start, end = shas.index(pass_sha), shas.index(fail_sha)
    if start >= end:
        return None
    return [_entry(commit) for commit in chain[start + 1:end + 1]]


def _unchanged(chain, first, second):
    """True only when two different SHAs are both known and no commit between them changed."""
    shas = [commit['sha'] for commit in chain]
    if first == second or first not in shas or second not in shas:
        return False
    low, high = sorted((shas.index(first), shas.index(second)))
    return all(chain[index].get('changed') is False for index in range(low + 1, high + 1))


def _test_ids(run):
    """Return the failing test IDs, or None when missing or unparsable."""
    tests = run.get('failed_tests')
    if not isinstance(tests, list) or not tests:
        return None
    if not all(isinstance(test, str) and test.strip() for test in tests):
        return None
    return frozenset(tests)


def _failing_streak(runs, sha, newest_first):
    """Failing runs of sha newer than the newest pass of the same check, newest first."""
    stopped, out = set(), []
    for kind, run in _runs_on(runs, sha, newest_first):
        if kind in stopped:
            continue
        if _passed(kind, run):
            stopped.add(kind)
        elif run['conclusion'] in BAD:
            out.append((kind, run))
    return out


def _env_evidence(kind, run):
    """Positive ENV evidence from one run; a run that did not fail gives none."""
    if not isinstance(run, dict) or run.get('conclusion') not in BAD:
        return []
    found = []
    if run['conclusion'] in ENV_CONCLUSIONS:
        found.append({'code': 'ENV_CONCLUSION', 'kind': kind, 'run': run.get('id'),
                      'detail': run['conclusion']})
    step = run.get('failed_step')
    if not isinstance(step, str):
        step = run.get('step_name')
    if isinstance(step, str) and _SETUP_STEP.search(step) and not _TEST_TEXT.search(step):
        found.append({'code': 'ENV_SETUP_STEP', 'kind': kind, 'run': run.get('id'),
                      'detail': step})
    reason = run.get('reason')
    if isinstance(reason, str) and _ENV_REASON.search(reason) and not _TEST_TEXT.search(reason):
        found.append({'code': 'ENV_REASON', 'kind': kind, 'run': run.get('id'),
                      'detail': reason})
    return found


def _reproduced(tested):
    """{test: run ids} for tests failing on Linux and FULL Mac, or on two FULL Mac runs."""
    out = {}
    for test in sorted(set().union(*(ids for _, _, ids in tested))):
        linux = {run['id'] for kind, run, ids in tested if kind == LINUX and test in ids}
        full = {run['id'] for kind, run, ids in tested if kind == FULL_MAC and test in ids}
        if (linux and full) or len(full) >= 2:
            out[test] = sorted(linux | full)
    return out


def _later_passes(tested, runs, commits, fail_sha, newest_first):
    """Evidence that every failing check later passed elsewhere with no change, else None."""
    ordered = _ordered(runs, newest_first)
    positions = {id(run): index for index, run in enumerate(ordered)}
    chain = _commits(commits, newest_first)[::-1]
    evidence = []
    for kind in sorted({kind for kind, _, _ in tested}):
        newest_fail = min(positions[id(run)] for k, run, _ in tested if k == kind)
        later = next((run for run in ordered[:newest_fail]
                      if run_kind(run) == kind and _completed(run) and _passed(kind, run) and
                      run['head_sha'] != fail_sha and
                      _unchanged(chain, fail_sha, run['head_sha'])), None)
        if later is None:
            return None
        tests = set().union(*(ids for k, _, ids in tested if k == kind))
        evidence.append({'code': 'LATER_PASS', 'kind': kind, 'run': later['id'],
                         'sha': later['head_sha'], 'tests': sorted(tests)})
    return evidence


def classify(fail_sha, commits, runs, newest_first=True):
    """Return (class, evidence list) for the failing runs of fail_sha."""
    failing = _failing_streak(runs, fail_sha, newest_first)
    if not failing:
        return UNKNOWN, [{'code': 'NO_FAILING_RUN', 'sha': fail_sha}]
    evidence, tested, unexplained, env_runs = [], [], False, 0
    for kind, run in failing:
        env = _env_evidence(kind, run)
        evidence.extend(env)
        ids = _test_ids(run)
        if ids:
            tested.append((kind, run, ids))
            evidence.append({'code': 'TEST_FAILURE', 'kind': kind, 'run': run['id'],
                             'tests': sorted(ids)})
        elif env:
            env_runs += 1
        else:
            unexplained = True
            evidence.append({'code': 'TEST_ID_UNAVAILABLE', 'kind': kind, 'run': run['id']})
    reproduced = _reproduced(tested) if tested else {}
    for test, run_ids in reproduced.items():
        evidence.append({'code': 'REPRODUCED', 'test': test, 'runs': run_ids})
    later = (_later_passes(tested, runs, commits, fail_sha, newest_first)
             if tested and not unexplained else None)
    evidence.extend(later or [])
    if reproduced and later:
        evidence.append({'code': 'CONFLICT', 'detail': 'reproduced and later passed unchanged'})
        return UNKNOWN, evidence
    if reproduced:
        return CODE, evidence
    if later:
        return FLAKY, evidence
    if not tested and env_runs == len(failing):
        return ENV, evidence
    return UNKNOWN, evidence


def classify_incident(commits, runs, newest_first=True):
    """Find the incident heads, list the range and classify; return a new dict."""
    pass_sha, fail_sha = find_heads(commits, runs, newest_first)
    result = {'incident': fail_sha is not None, 'pass_sha': pass_sha, 'fail_sha': fail_sha,
              'range': None, 'class': UNKNOWN, 'evidence': []}
    if fail_sha is None:
        result['evidence'] = [{'code': 'NO_FAILING_HEAD'}]
        return result
    result['range'] = commit_range(commits, pass_sha, fail_sha, newest_first)
    result['class'], evidence = classify(fail_sha, commits, runs, newest_first)
    if result['range'] is not None:
        evidence.append({'code': 'RANGE', 'commits': len(result['range']),
                         'prs': [item['pr'] for item in result['range'] if item['pr'] is not None]})
    result['evidence'] = evidence
    return result
