"""Read-only classification of a main-branch CI incident (#1706 R1).

Every function takes plain CI data as an argument.  Nothing here reads or
writes GitHub, so tests can exercise the whole classification without a
network.  A run is a mapping with ``id``, ``status``, ``conclusion``,
``head_sha`` and a workflow marker (``workflow``/``source`` or a check/job
name).  Optional evidence fields are ``failed_step``/``step_name``,
``reason``, failing test ids, passing test ids and ``jobs`` for FULL trust.
"""

from __future__ import annotations

from .mac_full_due import FAILED, JOBS

ENV = 'ENV'
FLAKY = 'FLAKY'
CODE = 'CODE'
UNKNOWN = 'UNKNOWN'
CLASSES = (ENV, FLAKY, CODE, UNKNOWN)

LINUX = 'linux'
MAC = 'mac'

# A run can be environmental without the conclusion being plain failure.
ENV_CONCLUSIONS = frozenset({'cancelled', 'timed_out', 'startup_failure'})
FAILING_CONCLUSIONS = frozenset(FAILED) | ENV_CONCLUSIONS

_TEST_STEP_WORDS = ('test', 'pytest', 'shard', 'suite', 'tox', 'unittest')
_CAPACITY_WORDS = ('capacity', 'no runner', 'runner unavailable', 'runner shortage')
_FAIL_TEST_KEYS = ('test_id', 'failed_test', 'failing_test', 'test_name', 'test',
                   'failed_tests', 'test_failures')
_PASS_TEST_KEYS = ('test_id', 'passed_test', 'passed_tests', 'test')
_PR_KEYS = ('pr', 'pr_number', 'pull_number')


def _ordered(runs, newest_first):
    """Return dict runs in newest-first order without changing the input."""
    if not isinstance(runs, (list, tuple)):
        return []
    items = [run for run in runs if type(run) is dict]
    return items if newest_first else list(reversed(items))


def _sha(run):
    value = run.get('head_sha')
    return value if type(value) is str and value else None


def _label(run):
    kind = _kind(run) or 'ci'
    run_id = run.get('id')
    return f'{kind} run {run_id}' if run_id is not None else kind


def _kind(run):
    """Return LINUX, MAC or None from the supplied workflow/check markers."""
    for key in ('workflow', 'source', 'pipeline', 'kind', 'suite'):
        value = run.get(key)
        if type(value) is str:
            text = value.lower()
            if 'mac' in text or 'full' in text:
                return MAC
            if 'linux' in text or 'docs' in text:
                return LINUX
    for key in ('name', 'check_name', 'job_name'):
        value = run.get(key)
        if type(value) is str:
            text = value.lower()
            if 'macos' in text or 'full' in text:
                return MAC
            if 'linux' in text or 'docs' in text:
                return LINUX
    jobs = run.get('jobs')
    if type(jobs) is list and any(
            type(job) is dict and 'shard' in str(job.get('name', '')).lower()
            for job in jobs):
        return MAC
    return None


def _completed(run):
    return run.get('status') in (None, 'completed')


def _linux_pass(run):
    return _completed(run) and run.get('conclusion') == 'success'


def _mac_trusted(run):
    """A FULL success needs every shard job and the check job to pass."""
    if not (_completed(run) and run.get('conclusion') == 'success'):
        return False
    jobs = run.get('jobs')
    if jobs is None:
        return True
    if type(jobs) is not list:
        return False
    done = {job.get('name'): job.get('conclusion')
            for job in jobs if type(job) is dict}
    return all(done.get(name) == 'success' for name in JOBS)


def _is_failure(run):
    """A run whose own conclusion is a failure, timeout or startup failure."""
    return run.get('conclusion') in FAILED


def _is_failing(run):
    """Any run that carries environment or test-failure evidence."""
    return run.get('conclusion') in FAILING_CONCLUSIONS


def _failed_step(run):
    for key in ('failed_step', 'failed_step_name', 'step_name'):
        value = run.get(key)
        if type(value) is str and value.strip():
            return value.strip()
    return None


def _is_test_step(step):
    if type(step) is not str:
        return False
    text = step.lower()
    return any(word in text for word in _TEST_STEP_WORDS)


def _is_capacity(value):
    if type(value) is not str:
        return False
    text = value.lower()
    return any(word in text for word in _CAPACITY_WORDS)


def _env_evidence(runs):
    """Positive environment evidence, never from a successful run."""
    evidence = []
    for run in _ordered(runs, True):
        if not _is_failing(run):
            continue
        label = _label(run)
        conclusion = run.get('conclusion')
        if conclusion in ENV_CONCLUSIONS:
            evidence.append(f'{label}: {conclusion}')
        step = _failed_step(run)
        reason = run.get('reason')
        if _is_capacity(step) or _is_capacity(reason):
            evidence.append(f'{label}: runner capacity cancel')
        elif step is not None and not _is_test_step(step):
            evidence.append(f'{label}: non-test step {step!r} failed')
    return evidence


def _string_ids(value):
    if type(value) is str:
        return [value.strip()] if value.strip() else []
    if type(value) is list:
        found = []
        for item in value:
            if type(item) is str and item.strip():
                found.append(item.strip())
            elif type(item) is dict:
                for key in ('test_id', 'id', 'name'):
                    candidate = item.get(key)
                    if type(candidate) is str and candidate.strip():
                        found.append(candidate.strip())
                        break
        return found
    return []


def _raw_test_present(run):
    for key in _FAIL_TEST_KEYS:
        if key not in run:
            continue
        value = run[key]
        if type(value) is str or (type(value) is list and value):
            return True
        if value is not None and type(value) not in (str, list):
            return True
    return False


def _failed_ids(run):
    ids = []
    for key in _FAIL_TEST_KEYS:
        if key in run:
            ids.extend(_string_ids(run[key]))
    tests = run.get('tests')
    if type(tests) is list:
        for item in tests:
            if type(item) is not dict:
                continue
            if item.get('conclusion') not in FAILING_CONCLUSIONS:
                continue
            ids.extend(_string_ids(item.get('test_id', item.get('id'))))
    return list(dict.fromkeys(ids))


def _has_test_signal(run):
    return _raw_test_present(run) or _is_test_step(_failed_step(run))


def _passed_ids(run):
    """Explicit passing test ids, or None when success covers the whole suite."""
    if run.get('conclusion') != 'success':
        return set()
    explicit = False
    ids = set()
    for key in _PASS_TEST_KEYS:
        if key not in run:
            continue
        value = run[key]
        if type(value) is str:
            explicit = True
            if value.strip():
                ids.add(value.strip())
        elif type(value) is list:
            if value:
                explicit = True
                ids.update(_string_ids(value))
    tests = run.get('tests')
    if type(tests) is list:
        for item in tests:
            if type(item) is not dict or item.get('conclusion') != 'success':
                continue
            explicit = True
            ids.update(_string_ids(item.get('test_id', item.get('id'))))
    return ids if explicit else None


def _unique_runs(runs):
    found = []
    seen = set()
    for run in runs:
        run_id = run.get('id')
        marker = ('id', run_id) if type(run_id) is int else ('object', id(run))
        if marker in seen:
            continue
        seen.add(marker)
        found.append(run)
    return found


def _code_evidence(failures):
    """Independent reproduction: Linux+FULL at one SHA, or two FULL runs."""
    groups = {}
    for run in failures:
        if not _is_failure(run):
            continue
        for test_id in _failed_ids(run):
            groups.setdefault(test_id, []).append(run)
    evidence = []
    for test_id in sorted(groups):
        group = _unique_runs(groups[test_id])
        linux = [run for run in group if _kind(run) == LINUX]
        mac = [run for run in group if _kind(run) == MAC]
        reproduced = False
        for left in linux:
            for right in mac:
                if _sha(left) is not None and _sha(left) == _sha(right):
                    evidence.append(
                        f"test {test_id!r} failed on Linux and FULL Mac at {_sha(left)}")
                    reproduced = True
        if len(mac) >= 2:
            evidence.append(
                f"test {test_id!r} failed in {len(mac)} FULL Mac runs")
            reproduced = True
        if reproduced:
            break
    return evidence


def _flaky_evidence(ordered, changed):
    """A newer run on a different SHA passed the same test and nothing changed."""
    if changed:
        return []
    for fail_index, failure in enumerate(ordered):
        if not _is_failure(failure):
            continue
        failed_ids = _failed_ids(failure)
        if not failed_ids:
            continue
        fail_sha = _sha(failure)
        for later in ordered[:fail_index]:
            if later.get('conclusion') != 'success':
                continue
            if _kind(later) == MAC and not _mac_trusted(later):
                continue
            later_sha = _sha(later)
            if later_sha is None or later_sha == fail_sha:
                continue
            covered = _passed_ids(later)
            for test_id in failed_ids:
                if covered is not None and test_id not in covered:
                    continue
                return [
                    f"test {test_id!r} failed at {fail_sha or 'unknown SHA'} "
                    f"({_label(failure)}) and passed later at {later_sha} "
                    f"({_label(later)})"
                ]
    return []


def _newest_decisive(group, kind):
    """Newest completed PASS/FAIL run of a kind; cancels do not hide a result."""
    for item in group:
        if _kind(item) != kind or not _completed(item):
            continue
        if item.get('conclusion') in FAILED or item.get('conclusion') == 'success':
            return item
    return None


def trusted_pass(runs, *, newest_first=True):
    """Return the newest head whose required main checks are a trusted PASS."""
    ordered = _ordered(runs, newest_first)
    seen = set()
    for run in ordered:
        sha = _sha(run)
        if sha is None or sha in seen:
            continue
        seen.add(sha)
        group = [item for item in ordered if _sha(item) == sha]
        linux = _newest_decisive(group, LINUX)
        mac = _newest_decisive(group, MAC)
        if linux is None and mac is None:
            continue
        if linux is not None and not _linux_pass(linux):
            continue
        if mac is not None and not _mac_trusted(mac):
            continue
        return sha
    return None


def head_failed(runs, *, newest_first=True):
    """Return the newest head with a failing run, or None."""
    for run in _ordered(runs, newest_first):
        if _is_failure(run) and _sha(run) is not None:
            return _sha(run)
    return None


def head_failed_run(runs, *, newest_first=True):
    for run in _ordered(runs, newest_first):
        if _is_failure(run):
            return run
    return None


def _pr_numbers(run):
    numbers = set()
    for key in _PR_KEYS:
        value = run.get(key)
        if type(value) is int and not isinstance(value, bool) and value > 0:
            numbers.add(value)
    for key in ('prs', 'pull_requests', 'pulls'):
        value = run.get(key)
        if type(value) is not list:
            continue
        for item in value:
            if type(item) is int and not isinstance(item, bool) and item > 0:
                numbers.add(item)
            elif type(item) is dict:
                for number_key in ('number', 'pr', 'id'):
                    number = item.get(number_key)
                    if type(number) is int and not isinstance(number, bool) and number > 0:
                        numbers.add(number)
                        break
    return numbers


def incident_range(runs, trusted_sha, failing_sha, *, newest_first=True):
    """Return commits and PRs between the trusted PASS and the failing head."""
    ordered = _ordered(runs, newest_first)
    shas = [_sha(run) for run in ordered]
    try:
        fail_index = shas.index(failing_sha)
        pass_index = shas.index(trusted_sha)
    except ValueError:
        return {'commits': [], 'prs': []}
    if fail_index >= pass_index:
        return {'commits': [], 'prs': []}
    commits = []
    prs = set()
    for run in ordered[fail_index:pass_index]:
        sha = _sha(run)
        if sha is not None and sha not in commits:
            commits.append(sha)
        prs.update(_pr_numbers(run))
    return {'commits': commits, 'prs': sorted(prs)}


def commit_range(runs, trusted_sha, failing_sha, *, newest_first=True):
    return incident_range(runs, trusted_sha, failing_sha,
                          newest_first=newest_first)['commits']


def pr_range(runs, trusted_sha, failing_sha, *, newest_first=True):
    return incident_range(runs, trusted_sha, failing_sha,
                          newest_first=newest_first)['prs']


def classify_incident(runs, *, newest_first=True, changed=False):
    """Return (ENV|FLAKY|CODE|UNKNOWN, evidence) for the supplied CI runs.

    ENV is final only when no failing run carries a test failure.  A cancel or
    superseded run therefore never hides independent CODE reproduction.
    """
    ordered = _ordered(runs, newest_first)
    if not ordered:
        return UNKNOWN, ['no CI run data']
    failures = [run for run in ordered if _is_failure(run)]
    test_failures = [run for run in failures if _has_test_signal(run)]
    env_evidence = _env_evidence(ordered)
    if test_failures:
        flaky = _flaky_evidence(ordered, changed)
        if flaky:
            return FLAKY, flaky
        code = _code_evidence(failures)
        if code:
            return CODE, code
        if not any(_failed_ids(run) for run in test_failures):
            return UNKNOWN, ['failing run has no usable test ID']
        return UNKNOWN, ['no independent reproduction evidence']
    if env_evidence:
        return ENV, env_evidence
    return UNKNOWN, ['no failing run or environment evidence']


# Readable aliases for callers naming the lookups directly.
find_trusted_pass = trusted_pass
find_failing_head = head_failed
range_between = incident_range
classify = classify_incident

__all__ = [
    'ENV', 'FLAKY', 'CODE', 'UNKNOWN', 'CLASSES', 'LINUX', 'MAC',
    'trusted_pass', 'head_failed', 'head_failed_run', 'incident_range',
    'commit_range', 'pr_range', 'classify_incident', 'find_trusted_pass',
    'find_failing_head', 'range_between', 'classify',
]
