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

#1706 R2 (design sections 4 and 5) adds candidate evidence on the same terms:
``candidate_rows`` (one row per merged PR with flags), ``attribute`` (SINGLE or
BATCH), ``bisect_plan`` / ``bisect_first_failing`` and ``dependency_check``.
The import closure comes from ``main_incident_graph``; callers read every file
text and diff with ``git show`` themselves and pass them in.
"""

from __future__ import annotations

import copy
import math
import re

from . import mac_full_due
from . import main_incident_graph as graph

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


# #1706 R2: candidate evidence (design sections 4 and 5). Pure, like R1 above.

SINGLE = 'SINGLE'
BATCH = 'BATCH'
NOT_RUN = 'NOT_RUN'
REVERT_DEPENDENT = 'REVERT_DEPENDENT_LATER_MERGE'
NO_DEPENDENT = 'NO_DEPENDENT_LATER_MERGE'
DEPENDENCY_UNKNOWN = 'REVERT_DEPENDENCY_UNKNOWN'
EDITS_SCOPE_FILE = 'EDITS_SCOPE_FILE'
IMPORTS_DELETED_MODULE = 'IMPORTS_DELETED_MODULE'
USES_ADDED_NAME = 'USES_ADDED_NAME'
_OWN_STATES = (PASS, NOT_RUN)
_OWN_RANK = {NOT_RUN: 0, UNKNOWN: 1, PASS: 2}
_REQUIREMENTS = re.compile(r'requirements[\w.-]*\.(?:txt|in)\Z')
_MAKEFILES = frozenset({'Makefile', 'makefile', 'GNUmakefile'})


def _task(value):
    return value if isinstance(value, str) and value.strip() else UNKNOWN


def _paths(value):
    """Sorted unique non-empty path strings, or None when value is not a list of them."""
    if not isinstance(value, (list, tuple)):
        return None
    if not all(isinstance(item, str) and item for item in value):
        return None
    return sorted(set(value))


def _repo_path(path):
    """Repository-relative form of an import_closure path; those always lie in tools/llmtool."""
    path = path[2:] if path.startswith('./') else path
    return path if path.startswith(graph.PREFIX) else graph.PREFIX + path


def _closure_paths(closure):
    """Paths of an import_closure result, a list of results, or a list of paths.

    import_closure keeps the path form of its input map, which may be relative
    to tools/llmtool; its paths are made repository relative here so they match
    changed files from Git. A plain list of paths is taken as given.
    """
    items = [closure] if isinstance(closure, dict) else closure
    paths = set()
    if not isinstance(items, (list, tuple, set, frozenset)):
        return paths
    for item in items:
        if isinstance(item, str):
            paths.add(item)
        elif isinstance(item, dict) and isinstance(item.get('files'), list):
            paths.update(_repo_path(path) for path in item['files'] if isinstance(path, str))
    return paths


def _ci_or_env(path):
    """A workflow, requirements file, Makefile or worktree_env change."""
    base = path.rsplit('/', 1)[-1]
    return (path.startswith('.github/workflows/') or base in _MAKEFILES or
            bool(_REQUIREMENTS.match(base)) or 'worktree_env' in base)


def _test_file(path, tests):
    """True when path is the file of a failing test (given as test ID or as path)."""
    if path in tests:
        return True
    name = graph.module_name(path)
    if name is None or graph.is_package(path):
        return False
    return any(test == name or test.startswith(name + '.') for test in tests)


def _members(value):
    """Member Tasks of a merge-train integration PR, each {'pr', 'task_id'}."""
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        if isinstance(item, dict):
            pr = item.get('pr')
            out.append({'pr': pr if type(pr) is int else None, 'task_id': _task(item.get('task_id'))})
        else:
            out.append({'pr': None, 'task_id': _task(item)})
    return out


def _own_ci(own_ci, pr, tests):
    """(summary, signal, {test: state}) of the PR's own CI for the failing tests.

    own_ci[pr] is {'signal': LINUX or FULL_MAC, 'modules': {test module: state}}
    for the failing signal; the most specific module that is a dotted prefix of
    the test ID gives its state. Only PASS and NOT_RUN are kept; anything else,
    a missing entry or a missing module is UNKNOWN. A module state only counts
    for a signal that is exactly LINUX or FULL_MAC: with any other signal (unknown,
    misspelt or not a string) every state and the summary are UNKNOWN.
    """
    entry = own_ci.get(pr) if isinstance(own_ci, dict) and pr is not None else None
    entry = entry if isinstance(entry, dict) else {}
    signal = entry.get('signal')
    signal = signal if type(signal) is str and signal in KINDS else UNKNOWN
    modules = entry.get('modules') if signal != UNKNOWN else None
    modules = modules if isinstance(modules, dict) else {}
    per_test = {}
    for test in tests:
        keys = [key for key in modules if isinstance(key, str) and key and
                (test == key or test.startswith(key + '.'))]
        state = modules[max(keys, key=len)] if keys else None
        per_test[test] = state if isinstance(state, str) and state in _OWN_STATES else UNKNOWN
    states = set(per_test.values())
    if not states or UNKNOWN in states:
        summary = UNKNOWN
    elif NOT_RUN in states:
        summary = NOT_RUN
    else:
        summary = PASS
    return summary, signal, per_test


def _row(item, closure, tests, own_ci):
    pr = item.get('pr')
    pr = pr if type(pr) is int else None
    files = _paths(item.get('files'))
    members = _members(item.get('members'))
    row = {'pr': pr, 'merge_sha': _sha(item.get('merge_sha', item.get('sha'))),
           'task_id': _task(item.get('task_id')), 'train': bool(members), 'members': members,
           'files': files}
    if files is None:
        # Unknown changed files: every flag is unknown (None), never False.
        row.update(touches_import_closure=None, closure_files=[], touches_test_file=None,
                   test_files=[], touches_ci_or_env=None, ci_env_files=[])
    else:
        hits = [path for path in files if path in closure]
        test_files = [path for path in files if _test_file(path, tests)]
        ci_env = [path for path in files if _ci_or_env(path)]
        row.update(touches_import_closure=bool(hits), closure_files=hits,
                   touches_test_file=bool(test_files), test_files=test_files,
                   touches_ci_or_env=bool(ci_env), ci_env_files=ci_env)
    row['own_ci'], row['own_ci_signal'], row['own_ci_tests'] = _own_ci(own_ci, pr, tests)
    return row


def candidate_rows(range_prs, closure, failing_tests, own_ci):
    """Return one new row per merged PR in the incident range, in the given order.

    range_prs items: ``pr`` (int), ``merge_sha`` (or ``sha``), ``task_id``,
    ``files`` (changed paths, repository relative as Git reports them) and,
    for a merge-train integration PR, ``members`` (Task IDs or {'pr', 'task_id'}):
    such a PR stays one row that lists its member Tasks. closure is an
    ``import_closure`` result, a list of them, or a list of paths. failing_tests
    are test IDs (or test file paths). own_ci is described in ``_own_ci``.
    A row whose changed files are unknown has every file flag None.
    """
    paths = _closure_paths(closure)
    tests = failing_tests if isinstance(failing_tests, (list, tuple)) else []
    tests = sorted({test for test in tests if isinstance(test, str) and test.strip()})
    items = range_prs if isinstance(range_prs, (list, tuple)) else []
    return [_row(item, paths, tests, own_ci) for item in items if isinstance(item, dict)]


def _flag_rank(value):
    return 1 if value is False else 0  # True and unknown (None) rank first


def _rank(row):
    return (_flag_rank(row.get('touches_import_closure')), _flag_rank(row.get('touches_test_file')),
            _flag_rank(row.get('touches_ci_or_env')), _OWN_RANK.get(row.get('own_ci'), 1))


def _bisect_row(rows, bisect):
    """(row, evidence reference) when bisect names exactly one row's merge SHA, else None."""
    if not isinstance(bisect, dict):
        return None
    sha, ref = _sha(bisect.get('first_failing_sha')), bisect.get('evidence')
    if sha is None or not isinstance(ref, str) or not ref.strip():
        return None
    hits = [row for row in rows if row.get('merge_sha') == sha]
    return (hits[0], ref) if len(hits) == 1 else None


def _single(row, basis, evidence):
    return {'attribution': SINGLE, 'basis': basis, 'pr': row.get('pr'),
            'merge_sha': row.get('merge_sha'), 'task_id': row.get('task_id'),
            'evidence': evidence, 'rows': [row]}


def attribute(rows, bisect=None):
    """Return SINGLE or BATCH attribution for candidate rows; a new dict.

    SINGLE only when there is exactly one candidate, or a recorded bisect
    ({'first_failing_sha': sha, 'evidence': reference}) names the merge SHA of
    exactly one row. Otherwise BATCH with every row, ordered by flags (closure,
    test file, CI/env, then own CI NOT_RUN before UNKNOWN before PASS; ties keep
    merge order); no BATCH field names one PR. A bisect that is given but does
    not validly name one row also gives BATCH, even with one candidate. A FULL
    Mac failure with more than one candidate is therefore BATCH by default.
    """
    items = rows if isinstance(rows, (list, tuple)) else []
    rows = [copy.deepcopy(row) for row in items if isinstance(row, dict)]
    found = _bisect_row(rows, bisect)
    if found is not None:
        return _single(found[0], 'BISECT', found[1])
    if bisect is None and len(rows) == 1:
        return _single(rows[0], 'ONLY_CANDIDATE', None)
    if bisect is not None:
        basis = 'BISECT_UNMATCHED'
    else:
        basis = 'MULTIPLE_CANDIDATES' if rows else 'NO_CANDIDATE'
    return {'attribution': BATCH, 'basis': basis, 'candidates': len(rows),
            'rows': sorted(rows, key=_rank)}


def bisect_limit(count):
    """Most probes a bisect over count merges may take: ceil(log2 count) + 1."""
    return 0 if count < 1 else math.ceil(math.log2(count)) + 1


def _merge_shas(merge_shas):
    if not isinstance(merge_shas, (list, tuple)):
        return None
    shas = [_sha(value) for value in merge_shas]
    if None in shas or len(set(shas)) != len(shas):
        return None
    return shas


def _bisect(shas, results):
    """(ordered probes, first failing sha or None) of a binary search over shas."""
    if not shas:
        return [], None
    # Probe 1 reruns the failing head with the probe method: if it does not
    # reproduce there, PASS results elsewhere mean nothing and the search stops.
    head = shas[-1]
    probes = [head]
    if results.get(head) != FAIL:
        return probes, None
    low, high = 0, len(shas) - 1  # the merge before shas[0] is the trusted PASS
    while low < high:
        mid = (low + high) // 2
        probes.append(shas[mid])
        state = results.get(shas[mid])
        if state == FAIL:
            high = mid
        elif state == PASS:
            low = mid + 1
        else:
            return probes, None
    return probes, shas[high]


def bisect_plan(merge_shas, results=None):
    """Return the ordered probe SHAs of a bisect; a new list, at most bisect_limit(n) long.

    merge_shas are the merges after the last trusted PASS through the failing
    head, oldest first. results maps probed SHAs to PASS or FAIL. The plan holds
    every probe whose result decides the search so far, then the next probe to
    run (the last entry without a result), if any. Invalid or duplicate SHAs
    give an empty plan.
    """
    shas = _merge_shas(merge_shas)
    if shas is None:
        return []
    return _bisect(shas, results if isinstance(results, dict) else {})[0]


def bisect_first_failing(merge_shas, results):
    """Return the first failing merge SHA once the recorded results decide it, else None."""
    shas = _merge_shas(merge_shas)
    if shas is None or not isinstance(results, dict):
        return None
    return _bisect(shas, results)[1]


def _names(text):
    """Module-level names of a text; set() for an absent file (None); None if unparsable."""
    return set() if text is None else graph.defined_names(text)


def _scope_changes(files):
    """(deleted modules {module: path}, added names {module: names}, unparsable records)."""
    deleted, added, unparsable = {}, {}, []
    for path in sorted(files):
        before, after = files[path]
        module = graph.module_name(path)
        if module is None:
            continue
        if (before is None) != (after is None):
            # One side has no file: the scope or its revert deletes this module.
            deleted[module] = path
        if after is None:
            continue
        new, old = _names(after), _names(before)
        if new is None or old is None:
            unparsable.append({'source': 'scope', 'pr': None, 'path': path})
            continue
        if new - old:
            added[module] = new - old
    return deleted, added, unparsable


def _scope_files(scope):
    files = scope.get('files') if isinstance(scope, dict) else None
    out = {}
    if isinstance(files, dict):
        for path, change in files.items():
            if isinstance(path, str) and path:
                change = change if isinstance(change, dict) else {}
                out[path] = (change.get('before'), change.get('after'))
    return out


def _dependent(merge, scope_files, deleted, added, unparsable):
    """The dependency record of one later merge, or None; appends unparsable records."""
    pr = merge.get('pr')
    pr = pr if type(pr) is int else None
    files = merge.get('files') if isinstance(merge.get('files'), dict) else {}
    edits, modules, names = set(), set(), set()
    for path in sorted(path for path in files if isinstance(path, str) and path):
        text = files[path]
        if path in scope_files:
            edits.add(path)
        if text is None or not path.endswith('.py'):
            continue
        imported, used = graph.imports(path, text), graph.used_names(path, text)
        if imported is None or used is None:
            unparsable.append({'source': 'later', 'pr': pr, 'path': path})
            continue
        modules.update(name for name in imported if name in deleted)
        for module, name in used:
            new = added.get(module, set())
            if name == '*':
                names.update(module + '.' + item for item in new)
            elif name in new:
                names.add(module + '.' + name)
    kinds = [kind for kind, hit in ((EDITS_SCOPE_FILE, edits), (IMPORTS_DELETED_MODULE, modules),
                                    (USES_ADDED_NAME, names)) if hit]
    if not kinds:
        return None
    return {'pr': pr, 'merge_sha': _sha(merge.get('merge_sha', merge.get('sha'))), 'kinds': kinds,
            'files': sorted(edits), 'modules': sorted(modules), 'names': sorted(names)}


def dependency_check(scope, later_merges):
    """Would reverting scope break a later merge? Pure; returns a new dict.

    scope is {'files': {path: {'before': text or None, 'after': text or None}}}:
    each file the scope changed, before and after the scope (None: no file).
    later_merges are the merges after the scope, each {'pr', 'merge_sha',
    'files': {path: text after that merge, or None when it deleted the file}}.

    A later merge depends on the scope when it edits a file in the scope
    (EDITS_SCOPE_FILE), imports a module that one side of the scope lacks, so
    the scope or its revert deletes it (IMPORTS_DELETED_MODULE), or uses a
    module-level name the scope adds (USES_ADDED_NAME). Any dependent gives
    REVERT_DEPENDENT_LATER_MERGE listing each such merge. With none, an empty
    scope or a text that does not parse gives REVERT_DEPENDENCY_UNKNOWN.
    """
    scope_files = _scope_files(scope)
    deleted, added, unparsable = _scope_changes(scope_files)
    dependents = []
    merges = later_merges if isinstance(later_merges, (list, tuple)) else []
    for merge in merges:
        if isinstance(merge, dict):
            found = _dependent(merge, scope_files, deleted, added, unparsable)
            if found is not None:
                dependents.append(found)
    if dependents:
        code = REVERT_DEPENDENT
    elif unparsable or not scope_files:
        code = DEPENDENCY_UNKNOWN
    else:
        code = NO_DEPENDENT
    return {'code': code, 'dependents': dependents, 'unparsable': unparsable}
