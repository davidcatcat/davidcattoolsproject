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

One shared rule reads runs everywhere (``newest_state``): pick the newest
recognized run of a check on a SHA first, then read its status. When that
newest run is queued or in progress the check is PENDING: it neither fails the
head, nor counts toward a trusted PASS, nor supplies failing runs, nor counts
as a later pass, whatever older completed runs say. ``trusted_pass``,
``head_failed``, ``check_states``, ``_failing_streak`` and ``_later_passes``
all go through it.

Classes: ENV needs positive environment evidence on every failing run and no
test failure; FLAKY needs a newer pass of the same check on a SHA that follows
the failing SHA in main history with no change in between, where that pass is
the newest run of the check on its SHA; CODE needs independent reproduction of
one test at the failing SHA (Linux and FULL Mac, or two FULL Mac runs).
Anything else, including a missing or unparsable test ID, is UNKNOWN.
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
PASS = 'PASS'
FAIL = 'FAIL'
PENDING = 'PENDING'
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
    """A completed Linux run passes on success; FULL Mac only when every FULL job succeeded too."""
    if not _completed(run) or run['conclusion'] != 'success':
        return False
    if kind == LINUX:
        return True
    done = {job.get('name'): job.get('conclusion') for job in run['jobs'] if isinstance(job, dict)}
    return all(done.get(name) == 'success' for name in mac_full_due.JOBS)


def _failed(run):
    return _completed(run) and run['conclusion'] in BAD


def _runs_on(runs, sha, newest_first):
    """Completed required-check runs of sha, newest first, as (kind, run)."""
    out = []
    for run in _ordered(runs, newest_first):
        kind = run_kind(run)
        if kind and run['head_sha'] == sha and _completed(run):
            out.append((kind, run))
    return out


def newest_runs(runs, sha, newest_first=True):
    """Return {kind: newest recognized run} for sha, whatever its status.

    The newest run is chosen before any status filter, so a queued or in-progress
    rerun hides every older completed run of that check.
    """
    newest = {}
    for run in _ordered(runs, newest_first):
        kind = run_kind(run)
        if kind and run['head_sha'] == sha:
            newest.setdefault(kind, run)
    return newest


def check_state(kind, run):
    """PENDING, PASS or FAIL for one run of kind; None for any other completed result."""
    if not _completed(run):
        return PENDING
    if _passed(kind, run):
        return PASS
    if _failed(run):
        return FAIL
    return None


def newest_state(runs, sha, kind, newest_first=True):
    """Return (newest recognized run of kind on sha, its state), or (None, None).

    The one shared rule for reading runs: the newest run is chosen first and
    only then is its status read, so a pending newest run is PENDING.
    """
    run = newest_runs(runs, sha, newest_first).get(kind)
    return (None, None) if run is None else (run, check_state(kind, run))


def check_states(runs, sha, newest_first=True):
    """Return {kind: state of the newest run} for sha; a kind with no run is absent."""
    states = {}
    for kind in KINDS:
        run, state = newest_state(runs, sha, kind, newest_first)
        if run is not None:
            states[kind] = state
    return states


def trusted_pass(runs, sha, newest_first=True):
    """True only when the newest Linux run passed and the newest FULL Mac run is trusted.

    A pending newest run of either check is never a trusted PASS.
    """
    states = check_states(runs, sha, newest_first)
    return all(states.get(kind) == PASS for kind in KINDS)


def head_failed(runs, sha, newest_first=True):
    """True when the newest run of any required check on sha completed and did not pass.

    A failed run followed by a green rerun on the same SHA is not failing, and a
    failed run followed by a queued or in-progress rerun is PENDING, not failing.
    """
    return FAIL in check_states(runs, sha, newest_first).values()


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


def _unchanged_after(chain, earlier, later):
    """True only when later follows earlier in main history with no change in between.

    chain is oldest first. A SHA that is unknown, equal to earlier or older than
    earlier is never a later SHA, so history is read in one direction only.
    """
    shas = [commit['sha'] for commit in chain]
    if earlier not in shas or later not in shas:
        return False
    start, end = shas.index(earlier), shas.index(later)
    if start >= end:
        return False
    return all(chain[index].get('changed') is False for index in range(start + 1, end + 1))


def _test_ids(run):
    """Return the failing test IDs, or None when missing or unparsable."""
    tests = run.get('failed_tests')
    if not isinstance(tests, list) or not tests:
        return None
    if not all(isinstance(test, str) and test.strip() for test in tests):
        return None
    return frozenset(tests)


def _failing_streak(runs, sha, newest_first):
    """Failing runs of sha newer than the newest pass of the same check, newest first.

    Only a check whose newest run (shared rule) is FAIL supplies runs; a check
    whose newest run is pending, passed or neither supplies none.
    """
    states = check_states(runs, sha, newest_first)
    stopped = {kind for kind in KINDS if states.get(kind) != FAIL}
    out = []
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


def _later_pass(ordered, chain, kind, fail_sha, newest_fail):
    """Newest run of kind that passed on a later unchanged SHA and is newer than newest_fail.

    The candidate must be the newest run of kind on its SHA (shared rule): a
    newer queued, in-progress or failed run on that SHA means no pass there.
    """
    for run in ordered[:newest_fail]:
        if run_kind(run) != kind:
            continue
        sha = run['head_sha']
        if sha == fail_sha or not _unchanged_after(chain, fail_sha, sha):
            continue
        newest, state = newest_state(ordered, sha, kind)
        if newest is run and state == PASS:
            return run
    return None


def _later_passes(tested, runs, commits, fail_sha, newest_first):
    """Evidence that every failing check later passed elsewhere with no change, else None."""
    ordered = _ordered(runs, newest_first)
    positions = {id(run): index for index, run in enumerate(ordered)}
    chain = _commits(commits, newest_first)[::-1]
    evidence = []
    for kind in sorted({kind for kind, _, _ in tested}):
        newest_fail = min(positions[id(run)] for k, run, _ in tested if k == kind)
        later = _later_pass(ordered, chain, kind, fail_sha, newest_fail)
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
