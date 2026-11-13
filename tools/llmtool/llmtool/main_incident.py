"""Read-only main-branch incident evidence (#1706 R1).

Everything in this module is pure: callers supply the CI run data already read
elsewhere, so importing or calling it never touches GitHub.  A *head record*
describes one main commit::

    {
        "sha": "a" * 40,
        "pr": 42,                       # optional pull request number
        "linux": {...},                 # required Linux check run
        "full": {...},                  # trusted FULL Mac workflow run
    }

A check or workflow run is a dict with ``status``, ``conclusion`` (a ``result``
or ``outcome`` key is also accepted), an optional positive integer ``run_id``
(``id`` is also accepted), and an optional ``test`` / ``test_id`` /
``failed_test`` / ``test_name``.  A FULL run also carries ``jobs``, either a
list of ``{"name": ..., "conclusion": ...}`` or a mapping of name to
conclusion.  A full success is trusted only when every job name in
``mac_full_due.JOBS`` succeeded.

Records are ordered newest first unless ``newest_first=False`` is passed.
"""

from __future__ import annotations

from . import mac_full_due

CLASSES = ('ENV', 'FLAKY', 'CODE', 'UNKNOWN')
ENV_CONCLUSIONS = frozenset({'cancelled', 'timed_out', 'startup_failure'})
FAILED_CONCLUSIONS = frozenset(mac_full_due.FAILED) | ENV_CONCLUSIONS
PASS_CONCLUSIONS = frozenset({'success', 'pass', 'passed'})
LINUX_CHECKS = frozenset({'check (Linux 3.14)', 'check (docs)'})
NON_TEST_STEPS = frozenset({
    'set up job', 'setup job', 'checkout', 'install', 'install dependencies',
    'cache', 'start runner', 'upload artifact', 'upload artifacts',
    'download artifact', 'download artifacts', 'prepare',
})
_CANCEL_WORDS = ('runner', 'capacity')


def _conclusion(run):
    """Return a lower-case conclusion-like value, or None."""
    if not isinstance(run, dict):
        return None
    for key in ('conclusion', 'result', 'outcome', 'status'):
        value = run.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def _run_id(run):
    """Return a positive integer run id, or None; booleans never count."""
    if not isinstance(run, dict):
        return None
    for key in ('run_id', 'id'):
        value = run.get(key)
        if type(value) is int and value > 0:
            return value
    return None


def _sha(record):
    if not isinstance(record, dict):
        return None
    for key in ('sha', 'head_sha', 'commit'):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _pr(record):
    if not isinstance(record, dict):
        return None
    for key in ('pr', 'pull', 'pr_number'):
        value = record.get(key)
        if type(value) is int and value > 0:
            return value
    return None


def _test_id(run):
    """Return a usable test id, or None when it is missing or unparsable."""
    if not isinstance(run, dict):
        return None
    for key in ('test', 'test_id', 'failed_test', 'test_name'):
        value = run.get(key)
        if (isinstance(value, str) and value.strip() and
                not any(ord(char) < 32 or ord(char) == 127 for char in value)):
            return value.strip()
    return None


def _runs(value):
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple)):
        return [run for run in value if isinstance(run, dict)]
    return []


def _linux_checks(record):
    if not isinstance(record, dict):
        return []
    return (_runs(record.get('linux')) + _runs(record.get('linux_runs')) +
            _runs(record.get('linux_check')))


def _full_runs(record):
    if not isinstance(record, dict):
        return []
    return (_runs(record.get('full')) + _runs(record.get('full_runs')) +
            _runs(record.get('full_run')))


def _newest(runs):
    """Return the run with the largest run id, falling back to list order."""
    newest = None
    newest_key = None
    for index, run in enumerate(runs):
        run_id = _run_id(run)
        key = (run_id if run_id is not None else -1, index)
        if newest_key is None or key > newest_key:
            newest, newest_key = run, key
    return newest


def _jobs_succeeded(run):
    jobs = run.get('jobs') if isinstance(run, dict) else None
    if isinstance(jobs, dict):
        done = {name: _conclusion(value) if isinstance(value, dict) else
                (value.strip().lower() if isinstance(value, str) else None)
                for name, value in jobs.items()}
    elif isinstance(jobs, list):
        done = {job.get('name'): _conclusion(job)
                for job in jobs if isinstance(job, dict) and isinstance(job.get('name'), str)}
    else:
        return False
    return all(done.get(name) == 'success' for name in mac_full_due.JOBS)


def trusted_full_pass(run):
    """True only for a completed FULL run whose every required job succeeded."""
    if not isinstance(run, dict):
        return False
    status = run.get('status')
    if isinstance(status, str) and status.strip().lower() != 'completed':
        return False
    return _conclusion(run) == 'success' and _jobs_succeeded(run)


def linux_passed(check):
    """True for a completed required Linux check that succeeded."""
    if not isinstance(check, dict):
        return False
    status = check.get('status')
    if isinstance(status, str) and status.strip().lower() != 'completed':
        return False
    name = check.get('name') or check.get('check_name')
    if isinstance(name, str) and name not in LINUX_CHECKS:
        return False
    return _conclusion(check) == 'success'


def trusted_pass(record):
    """A main PASS needs the required Linux check and a trusted FULL run."""
    return (linux_passed(_newest(_linux_checks(record))) and
            trusted_full_pass(_newest(_full_runs(record))))


def head_failed(record):
    """True when any supplied Linux check or FULL run is failed or environment-stopped."""
    runs = _linux_checks(record) + _full_runs(record)
    return any(_conclusion(run) in FAILED_CONCLUSIONS for run in runs)


def _ordered(records, newest_first):
    sequence = [record for record in records if isinstance(record, dict)]
    return sequence if newest_first else list(reversed(sequence))


def find_last_trusted_head(records, *, newest_first=True):
    """Return the newest head record that is a trusted main PASS."""
    for record in _ordered(records, newest_first):
        if trusted_pass(record):
            return record
    return None


def find_failing_head(records, *, newest_first=True):
    """Return the newest head record with a failed Linux check or FULL run."""
    for record in _ordered(records, newest_first):
        if head_failed(record):
            return record
    return None


def _index_of(records, target):
    for index, record in enumerate(records):
        if record is target:
            return index
    sha = _sha(target)
    if sha is not None:
        for index, record in enumerate(records):
            if _sha(record) == sha:
                return index
    return None


def _summary(record):
    return {'sha': _sha(record), 'pr': _pr(record)}


def commit_pr_range(records, last_good, failing, *, newest_first=True):
    """Return commits/PRs after ``last_good`` through ``failing``, oldest first."""
    sequence = _ordered(records, newest_first)
    failing_index = _index_of(sequence, failing)
    good_index = _index_of(sequence, last_good)
    if failing_index is None or good_index is None or failing_index >= good_index:
        return []
    return [_summary(record) for record in reversed(sequence[failing_index:good_index])]


def find_incident(records, *, newest_first=True):
    """Return the last trusted PASS, the failing head, and the range between them."""
    sequence = _ordered(records, newest_first)
    for index, record in enumerate(sequence):
        if not head_failed(record):
            continue
        last_good = next((candidate for candidate in sequence[index + 1:]
                          if trusted_pass(candidate)), None)
        return {
            'last_good': last_good,
            'failing': record,
            'range': commit_pr_range(sequence, last_good, record, newest_first=True)
            if last_good is not None else [],
        }
    return {'last_good': find_last_trusted_head(sequence), 'failing': None, 'range': []}


def _env_evidence(linux, full, sha):
    evidence = []
    for source, run in ([('Linux', check) for check in linux] +
                        [('FULL', workflow) for workflow in full]):
        conclusion = _conclusion(run)
        run_id = _run_id(run)
        label = f'{source} run {run_id}' if run_id is not None else source
        if conclusion in ENV_CONCLUSIONS:
            where = f' at {sha}' if sha else ''
            evidence.append(f'{label} concluded {conclusion}{where}')
            continue
        reason = (run.get('cancel_reason') or run.get('cancelled_reason') or
                  run.get('reason'))
        if isinstance(reason, str) and any(word in reason.lower() for word in _CANCEL_WORDS):
            evidence.append(f'{label} was cancelled by runner/capacity: {reason.strip()}')
        step = run.get('failed_step') or run.get('step_name')
        kind = run.get('step_kind') or run.get('step_type')
        if isinstance(step, str) and step.strip():
            step = step.strip()
            if ((isinstance(kind, str) and kind.strip().lower() in
                 ('setup', 'infra', 'non-test', 'non_test', 'runner')) or
                    step.lower() in NON_TEST_STEPS):
                evidence.append(f'{label} failed non-test step {step}')
    return list(dict.fromkeys(evidence))


def _failed_tests(linux, full):
    """Map each usable failing test id to its Linux and FULL runs."""
    tests = {}
    for source, runs in (('linux', linux), ('full', full)):
        for run in runs:
            if _conclusion(run) != 'failure':
                continue
            test = _test_id(run)
            if test is None:
                continue
            tests.setdefault(test, {'linux': [], 'full': []})[source].append(run)
    return tests


def _run_matches_sha(run, sha):
    run_sha = _sha(run)
    return run_sha is None or sha is None or run_sha == sha


def _code_evidence(tests, sha):
    evidence = []
    full_ids_by_test = {}
    for test, sources in tests.items():
        linux_runs = [run for run in sources['linux'] if _run_matches_sha(run, sha)]
        full_runs = [run for run in sources['full'] if _run_matches_sha(run, sha)]
        for linux_run in linux_runs:
            linux_id = _run_id(linux_run)
            for full_run in full_runs:
                full_id = _run_id(full_run)
                if linux_id is None or full_id is None:
                    continue
                evidence.append(
                    f'same test {test} failed at {sha or "the failing head"} '
                    f'in Linux run {linux_id} and FULL run {full_id}')
                break
        full_ids_by_test[test] = sorted({_run_id(run) for run in full_runs
                                         if _run_id(run) is not None})
    for test, run_ids in full_ids_by_test.items():
        if len(run_ids) >= 2:
            evidence.append(
                f'same test {test} failed in two FULL runs {run_ids[0]} and '
                f'{run_ids[1]} at {sha or "the failing head"}')
    return evidence


def _later_changed(run):
    for key in ('changed', 'change', 'has_changes', 'intervening_changes',
                'code_changed'):
        if run.get(key):
            return True
    for key in ('files', 'commits', 'changes'):
        value = run.get(key)
        if isinstance(value, (list, tuple, dict)) and value:
            return True
    return False


def _flaky_evidence(tests, later_passes, changed):
    if changed:
        return []
    evidence = []
    for run in _runs(later_passes):
        if _conclusion(run) not in PASS_CONCLUSIONS:
            continue
        test = _test_id(run)
        if test is None or test not in tests or _later_changed(run):
            continue
        run_id = _run_id(run)
        run_text = f'run {run_id}' if run_id is not None else 'a later run'
        evidence.append(
            f'same test {test} passed later on main in {run_text} with no change between')
    return evidence


def classify(failing, *, full_runs=None, later_passes=(), changed=False):
    """Return ``{"class": ENV|FLAKY|CODE|UNKNOWN, "evidence": [...]}``.

    ``failing`` is the failing head record.  ``full_runs`` overrides the FULL
    runs embedded in that record when supplied.  ``later_passes`` are later
    main run records for the same test; ``changed`` (or a ``changed`` flag on
    such a run) means code changed in between, so a later pass is not flaky.

    Positive environment evidence comes only from an explicit environment
    conclusion, a runner/capacity cancellation, or a failed non-test step.  A
    missing or unparsable test id is never environment evidence.
    """
    if not isinstance(failing, dict):
        return {'class': 'UNKNOWN', 'evidence': ['no failing head supplied']}
    sha = _sha(failing)
    linux = _linux_checks(failing)
    full = _runs(full_runs) if full_runs is not None else _full_runs(failing)

    environment = _env_evidence(linux, full, sha)
    if environment:
        return {'class': 'ENV', 'evidence': environment}

    tests = _failed_tests(linux, full)
    if not tests:
        return {
            'class': 'UNKNOWN',
            'evidence': ['failing test id is missing or unparsable; '
                         'no positive environment evidence'],
        }

    flaky = _flaky_evidence(tests, later_passes, changed)
    if flaky:
        return {'class': 'FLAKY', 'evidence': flaky}

    code = _code_evidence(tests, sha)
    if code:
        return {'class': 'CODE', 'evidence': code}

    return {
        'class': 'UNKNOWN',
        'evidence': ['no independent reproduction evidence and no later pass '
                     'without a change'],
    }


def classify_incident(records, *, later_passes=(), changed=False, newest_first=True):
    """Find the incident in ``records`` and classify its failing head."""
    incident = find_incident(records, newest_first=newest_first)
    if incident['failing'] is None:
        return dict(incident, **{'class': 'UNKNOWN',
                                 'evidence': ['no failing head in the supplied runs']})
    result = classify(incident['failing'], later_passes=later_passes, changed=changed)
    result.update(incident)
    return result