"""``llmtool ci main-incident scan|show|confirm`` (#1706 R3, design sections 5 and 9).

``scan`` reads main commits and CI runs through an injectable ``github_get``
(GET reads only; nothing is written to GitHub), runs the R1/R2 detection,
classification, candidate rows and attribution of ``main_incident`` and records
the result in the local forward-only ``main_incident_store``: it creates the
open incident, appends a new observation when the evidence changed, or moves
the open incident to RESOLVED (a newer trusted PASS and no failing head) or
SUPERSEDED (a different last-good head while main still fails). The PM session
that runs the first scan owns the incident; every later write needs that same
session (``pm_caller`` semantics).

``show --incident ID`` prints one record as JSON. ``confirm`` records a PM
revert-scope confirmation, bound with ``pm_caller.check_caller`` to the owning
PM session and to the digest of the newest observation. It never reverts.

Read plan (``github_get(path)`` is relative to the repository API):

* ``commits?sha=main&per_page=40``: main history, newest first.
* ``mac_full_due.RUNS`` and ``actions/runs/<id>/jobs``: FULL Mac runs.
* ``commits/<sha>/check-runs?check_name=check (Linux 3.14)``: Linux runs.
  Commits are walked from newest until a trusted PASS, at most MAX_COMMITS.
* For failed runs on the failing head only: ``actions/jobs/<id>`` (failed
  step) and ``check-runs/<id>/annotations`` (unittest ``FAIL:``/``ERROR:``
  lines give test IDs). These reads are best effort: a missing value stays
  unknown, so the class stays UNKNOWN rather than guessed.
* For range commits: ``commits/<sha>/pulls`` (PR number) and
  ``pulls/<n>/files`` (changed paths); a failed read leaves the value unknown.

Runs are ordered newest first within each kind (Linux check runs by ID,
FULL Mac runs as GitHub lists them); ``main_incident`` only compares runs of
one kind with each other. Commits carry no ``changed`` flag, so FLAKY needs a
caller that knows unchanged commits; scan never claims it. The import closure
needs file texts at the failing head; without an injected ``read_texts`` every
known-files row has ``touches_import_closure`` None (unknown), never False.
"""

from __future__ import annotations

import json
import re
import sys
from urllib.parse import quote

from . import main_incident as mi
from . import main_incident_graph as graph
from . import main_incident_store as store
from . import mac_full_due
from . import pm_caller
from .errors import LLMToolError

MAX_COMMITS = 40
MAX_FILES = 100
COMMITS = 'commits?sha=main&per_page=' + str(MAX_COMMITS)
LINUX_RUNS = 'commits/{sha}/check-runs?check_name=' + quote(mi.LINUX_CHECK, safe='') + \
    '&per_page=100'

NONE = 'MAIN_INCIDENT_NONE'
CREATED = 'MAIN_INCIDENT_CREATED'
UPDATED = 'MAIN_INCIDENT_UPDATED'
UNCHANGED = 'MAIN_INCIDENT_UNCHANGED'
RESOLVED = 'MAIN_INCIDENT_RESOLVED'
CONFIRMED = 'MAIN_INCIDENT_CONFIRMED'
UNAVAILABLE = 'MAIN_INCIDENT_STATE_UNAVAILABLE'
OPEN_AMBIGUOUS = 'MAIN_INCIDENT_OPEN_AMBIGUOUS'
NOT_OPEN = 'MAIN_INCIDENT_NOT_OPEN'
REVERT_CLASS_NOT_CODE = 'REVERT_CLASS_NOT_CODE'
REVERT_ATTRIBUTION_UNPROVEN = 'REVERT_ATTRIBUTION_UNPROVEN'
REVERT_SCOPE_INVALID = 'REVERT_SCOPE_INVALID'
REVERT_SCOPE_UNKNOWN_PR = 'REVERT_SCOPE_UNKNOWN_PR'
REVERT_REASON_MISSING = 'REVERT_REASON_MISSING'
REVERT_REASON_INVALID = 'REVERT_REASON_INVALID'

_TEST_LINE = re.compile(r'\b(?:FAIL|ERROR): (\w+) \(([\w.]+)\)')
_SCOPE_ITEM = re.compile(r'#?([1-9][0-9]{0,6})')


class Refused(Exception):
    """A typed refusal of one main-incident command."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def add_main_incident_parser(actions):
    """Add ``main-incident`` to the ``ci`` action subparsers."""
    incident = actions.add_parser('main-incident', help='Record and confirm main CI incidents')
    sub = incident.add_subparsers(dest='incident_action', required=True)
    scan = sub.add_parser('scan', help='Read main CI state (no GitHub writes); record the incident')
    scan.add_argument('--pm-session', default=None, help='caller PM session (else from the runtime)')
    show = sub.add_parser('show', help='Print one incident record as JSON')
    show.add_argument('--incident', required=True)
    confirm = sub.add_parser('confirm', help='Record the PM revert-scope confirmation')
    confirm.add_argument('--incident', required=True)
    confirm.add_argument('--scope', required=True, help='comma-separated PR numbers')
    confirm.add_argument('--reason', required=True)
    confirm.add_argument('--pm-session', default=None, help='caller PM session (else from the runtime)')
    return incident


# GitHub reads -----------------------------------------------------------------

def _read(github_get, path):
    try:
        data = github_get(path)
    except Exception:
        raise mac_full_due.StateError(path) from None
    if not isinstance(data, (dict, list)):
        raise mac_full_due.StateError(path)
    return data


def _optional(github_get, path):
    """A best-effort read: None on any failure."""
    try:
        return _read(github_get, path)
    except mac_full_due.StateError:
        return None


def _main_commits(github_get):
    data = _read(github_get, COMMITS)
    if not isinstance(data, list) or not data:
        raise mac_full_due.StateError(COMMITS)
    commits = []
    for item in data[:MAX_COMMITS]:
        sha = item.get('sha') if isinstance(item, dict) else None
        if not isinstance(sha, str) or not mac_full_due.SHA.match(sha):
            raise mac_full_due.StateError(COMMITS)
        commits.append({'sha': sha})
    return commits


def _linux_runs(github_get, sha):
    path = LINUX_RUNS.format(sha=sha)
    data = _read(github_get, path)
    runs = data.get('check_runs') if isinstance(data, dict) else None
    if not isinstance(runs, list):
        raise mac_full_due.StateError(path)
    out = []
    for run in runs:
        if not isinstance(run, dict) or run.get('name') != mi.LINUX_CHECK or \
                run.get('head_sha') != sha:
            continue
        if type(run.get('id')) is not int or not isinstance(run.get('status'), str):
            raise mac_full_due.StateError(path)
        out.append({'id': run['id'], 'name': run['name'], 'head_sha': sha,
                    'status': run['status'], 'conclusion': run.get('conclusion')})
    return out


def _steps(job):
    steps = job.get('steps') if isinstance(job, dict) else None
    if not isinstance(steps, list):
        return []
    return [{'name': step.get('name'), 'conclusion': step.get('conclusion')}
            for step in steps if isinstance(step, dict)]


def _mac_jobs(github_get, run_id):
    path = f'actions/runs/{run_id}/jobs?per_page=100'
    data = _read(github_get, path)
    jobs = data.get('jobs') if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        raise mac_full_due.StateError(path)
    return [{'id': job.get('id'), 'name': job.get('name'), 'conclusion': job.get('conclusion'),
             'steps': _steps(job)} for job in jobs if isinstance(job, dict)]


def _mac_runs(github_get):
    out = []
    for run in mac_full_due.full_runs(github_get):
        if type(run.get('id')) is not int:
            raise mac_full_due.StateError('run id')
        out.append({key: run.get(key) for key in
                    ('id', 'head_sha', 'head_branch', 'event', 'status', 'conclusion')})
    return out


def _ordered(linux, mac):
    return sorted(linux, key=lambda run: run['id'], reverse=True) + mac


def collect(github_get):
    """Return (walked commits newest first, runs) read from GitHub; raise StateError."""
    commits = _main_commits(github_get)
    mac = _mac_runs(github_get)
    linux, walked = [], []
    for commit in commits:
        sha = commit['sha']
        walked.append(dict(commit))
        linux.extend(_linux_runs(github_get, sha))
        for run in mac:
            if run['head_sha'] == sha and 'jobs' not in run:
                run['jobs'] = _mac_jobs(github_get, run['id'])
        if mi.trusted_pass(_ordered(linux, mac), sha):
            break
    return walked, _ordered(linux, mac)


def _pr_of(github_get, sha):
    pulls = _optional(github_get, f'commits/{sha}/pulls')
    if not isinstance(pulls, list):
        return None
    numbers = {pull.get('number') for pull in pulls if isinstance(pull, dict) and
               pull.get('merge_commit_sha') == sha and type(pull.get('number')) is int}
    return numbers.pop() if len(numbers) == 1 else None


def _pr_files(github_get, pr):
    if pr is None:
        return None
    data = _optional(github_get, f'pulls/{pr}/files?per_page={MAX_FILES}')
    # A full page may hide more files: the list is then unknown, never partial.
    if not isinstance(data, list) or len(data) >= MAX_FILES:
        return None
    names = [item.get('filename') for item in data if isinstance(item, dict)]
    if len(names) != len(data) or not all(isinstance(name, str) and name for name in names):
        return None
    return names


def _annotation_tests(github_get, check_ids):
    """Sorted test IDs from unittest lines in annotations; None when any read fails."""
    tests = set()
    for check_id in check_ids:
        items = _optional(github_get, f'check-runs/{check_id}/annotations?per_page=100')
        if not isinstance(items, list):
            return None
        for item in items:
            if not isinstance(item, dict):
                continue
            for field in ('title', 'message'):
                text = item.get(field)
                if not isinstance(text, str):
                    continue
                for name, where in _TEST_LINE.findall(text):
                    tests.add(where if where.endswith('.' + name) else where + '.' + name)
    return sorted(tests)


def _failure_detail(github_get, kind, run):
    if kind == mi.LINUX:
        check_ids = [run['id']]
        steps = _steps(_optional(github_get, f'actions/jobs/{run["id"]}'))
    else:
        failed = [job for job in run['jobs'] if job.get('conclusion') in mi.BAD]
        check_ids = [job['id'] for job in failed if type(job.get('id')) is int]
        if len(check_ids) != len(failed):
            check_ids = []
        steps = failed[0]['steps'] if failed else []
    detail = {}
    step = next((step['name'] for step in steps if step.get('conclusion') == 'failure' and
                 isinstance(step.get('name'), str)), None)
    if step is not None:
        detail['failed_step'] = step
    tests = _annotation_tests(github_get, check_ids) if check_ids else None
    if tests:
        detail['failed_tests'] = tests
    return detail


def _enrich(github_get, runs, fail_sha):
    """New run list with failure detail on the failed runs of the failing head."""
    out = []
    for run in runs:
        kind = mi.run_kind(run)
        if kind and run['head_sha'] == fail_sha and mi.check_state(kind, run) == mi.FAIL:
            run = dict(run, **_failure_detail(github_get, kind, run))
        out.append(run)
    return out


def _closure(read_texts, sha, tests):
    """(closure results, available); available only when every closure is complete."""
    if read_texts is None or not tests:
        return [], False
    try:
        texts = read_texts(sha)
    except Exception:
        return [], False
    if not isinstance(texts, dict):
        return [], False
    results = [graph.import_closure(test, texts) for test in tests]
    if not all(result['complete'] for result in results):
        return [], False
    return results, True


def scan_state(github_get, *, observed_at, read_texts=None):
    """Read GitHub and return {'pass_sha', 'fail_sha', 'observation' or None}."""
    commits, runs = collect(github_get)
    pass_sha, fail_sha = mi.find_heads(commits, runs)
    if fail_sha is None:
        return {'pass_sha': pass_sha, 'fail_sha': None, 'observation': None}
    for item in mi.commit_range(commits, pass_sha, fail_sha) or []:
        pr = _pr_of(github_get, item['sha'])
        if pr is not None:
            for commit in commits:
                if commit['sha'] == item['sha']:
                    commit['pr'] = pr
    runs = _enrich(github_get, runs, fail_sha)
    result = mi.classify_incident(commits, runs)
    states = mi.check_states(runs, fail_sha)
    signal = sorted(kind for kind, state in states.items() if state == mi.FAIL)
    failing_runs = [{'kind': kind, 'id': run['id'], 'conclusion': run['conclusion']}
                    for kind in signal for run in runs
                    if mi.run_kind(run) == kind and run['head_sha'] == fail_sha and
                    mi.check_state(kind, run) == mi.FAIL]
    tests = sorted({test for entry in result['evidence']
                    if entry.get('code') == 'TEST_FAILURE' for test in entry['tests']})
    range_prs = [{'pr': item['pr'], 'merge_sha': item['sha'],
                  'files': _pr_files(github_get, item['pr'])}
                 for item in result['range'] or []]
    closure, available = _closure(read_texts, fail_sha, tests)
    rows = mi.candidate_rows(range_prs, closure, tests, {})
    if not available:
        for row in rows:
            if row['files'] is not None:
                row['touches_import_closure'], row['closure_files'] = None, []
    observation = {
        'observed_at': observed_at, 'signal': signal, 'failing_head': fail_sha,
        'failing_runs': failing_runs, 'class': result['class'], 'evidence': result['evidence'],
        'range': result['range'], 'candidate_rows': rows, 'attribution': mi.attribute(rows),
        'closure_available': available,
    }
    return {'pass_sha': result['pass_sha'], 'fail_sha': fail_sha,
            'observation': json.loads(json.dumps(observation))}


# Commands ---------------------------------------------------------------------

class _OwnerView:
    """The incident store seen as the ``store`` that ``pm_caller`` reads owners from."""

    def __init__(self, root):
        self.root = root

    def get(self, incident):
        return store.get(incident, root=self.root)


def _caller(explicit, environ, state_root):
    caller = pm_caller.resolve_caller_session(explicit, environ)
    code = pm_caller.fence_refusal_code(caller, state_root=state_root)
    if code is not None:
        raise pm_caller.PMCallerRefused(code)
    return caller


def _require_owner(root, incident, caller):
    code = pm_caller.owner_refusal_code(_OwnerView(root), incident, caller)
    if code is not None:
        raise pm_caller.PMCallerRefused(code)


def _evidence_part(observation):
    return {key: value for key, value in observation.items() if key != 'observed_at'}


def _same(left, right):
    """True when two observations differ only in their observation time."""
    return _evidence_part(left) == _evidence_part(right)


def _summary(code, record, **extra):
    latest = record['observations'][-1]
    attribution = latest['attribution']
    return dict({'code': code, 'incident_id': record['incident_id'],
                 'outcome': record['outcome'], 'class': latest['class'],
                 'attribution': attribution['attribution'], 'basis': attribution.get('basis'),
                 'failing_head': latest['failing_head'],
                 'last_good_head': record['last_good_head']}, **extra)


def _scan(args, github_get, root, environ, state_root, now, read_texts):
    caller = _caller(getattr(args, 'pm_session', None), environ, state_root)
    at = store.utc_stamp(now)
    try:
        state = scan_state(github_get, observed_at=at, read_texts=read_texts)
    except mac_full_due.StateError as exc:
        return {'code': UNAVAILABLE, 'read': str(exc)}, 2
    opened = store.find_open(root)
    if len(opened) > 1:
        raise Refused(OPEN_AMBIGUOUS)
    current = opened[0] if opened else None
    pass_sha, observation = state['pass_sha'], state['observation']
    # Only a trusted PASS other than the recorded last-good head ends an incident:
    # a pending rerun on the failing head leaves it open.
    newer_pass = (current is not None and pass_sha is not None and
                  pass_sha != current['last_good_head'])
    extra = {}
    if newer_pass:
        _require_owner(root, current['incident_id'], caller)
        outcome = store.SUPERSEDED if observation is not None else store.RESOLVED
        ended = store.advance_outcome(current['incident_id'], current['record_sha256'], outcome,
                                      at=at, head=pass_sha, root=root)
        if observation is None:
            return _summary(RESOLVED, ended, resolved_head=pass_sha), 0
        extra['superseded'] = current['incident_id']
        current = None
    if observation is None:
        return {'code': NONE if current is None else UNCHANGED, 'last_good_head': pass_sha,
                'incident_id': None if current is None else current['incident_id']}, 0
    if current is None:
        record = store.create(store.new_record(pm_session_id=caller, observation=observation,
                                               last_good_head=pass_sha, created_at=at), root)
        return _summary(CREATED, record, **extra), 0
    _require_owner(root, current['incident_id'], caller)
    if _same(current['observations'][-1], observation):
        return _summary(UNCHANGED, current), 0
    record = store.append_observation(current['incident_id'], current['record_sha256'],
                                      observation, root)
    return _summary(UPDATED, record), 0


def _show(args, root):
    return store.get(args.incident, root), 0


def _scope(text):
    """Sorted unique PR numbers from ``1,#2, 3``; None when malformed or empty."""
    if not isinstance(text, str):
        return None
    prs = []
    for item in text.split(','):
        match = _SCOPE_ITEM.fullmatch(item.strip())
        if match is None:
            return None
        prs.append(int(match.group(1)))
    return sorted(set(prs)) if prs else None


def _required(attribution):
    """The PRs a scope must cover: the SINGLE PR, or every BATCH row."""
    if attribution['attribution'] == mi.SINGLE:
        return {attribution.get('pr')}
    return {row.get('pr') for row in attribution['rows']}


def _confirm(args, root, environ, state_root, now):
    caller = pm_caller.check_caller(_OwnerView(root), args.incident, explicit=args.pm_session,
                                    environ=environ, state_root=state_root)
    record = store.get(args.incident, root)
    if record['outcome'] != store.OPEN:
        raise Refused(NOT_OPEN)
    reason = args.reason.strip() if isinstance(args.reason, str) else ''
    if not reason:
        raise Refused(REVERT_REASON_MISSING)
    if not store.valid_reason(reason):
        raise Refused(REVERT_REASON_INVALID)
    scope = _scope(args.scope)
    if scope is None:
        raise Refused(REVERT_SCOPE_INVALID)
    index = len(record['observations']) - 1
    latest = record['observations'][index]
    if latest['class'] != mi.CODE:
        raise Refused(REVERT_CLASS_NOT_CODE)
    known = {row.get('pr') for row in latest['candidate_rows']}
    if not set(scope) <= known:
        raise Refused(REVERT_SCOPE_UNKNOWN_PR)
    # A BATCH is proven only as a whole: one PR (or any part) of it needs a
    # recorded bisect, which turns the attribution into SINGLE.
    required = _required(latest['attribution'])
    if None in required or not required <= set(scope):
        raise Refused(REVERT_ATTRIBUTION_UNPROVEN)
    confirmation = {'pm_session_id': caller, 'confirmed_at': store.utc_stamp(now),
                    'scope': scope, 'reason': reason, 'observation_index': index,
                    'evidence_sha256': store.observation_digest(latest)}
    updated = store.append_confirmation(args.incident, record['record_sha256'], confirmation,
                                        root)
    return {'code': CONFIRMED, 'incident_id': args.incident, 'confirmation': confirmation,
            'record_sha256': updated['record_sha256']}, 0


def run_main_incident(args, *, github_get=None, root=None, environ=None, state_root=None,
                      now=None, read_texts=None):
    """Print one JSON document; exit 0 done, 1 refused, 2 GitHub read unavailable."""
    action = args.incident_action
    try:
        if action == 'show':
            payload, status = _show(args, root)
        elif action == 'confirm':
            payload, status = _confirm(args, root, environ, state_root, now)
        else:
            if github_get is None:
                from .task_completion import _github_get as github_get
            payload, status = _scan(args, github_get, root, environ, state_root, now, read_texts)
    except pm_caller.PMCallerRefused as exc:
        payload, status = {'code': exc.code}, 1
    except Refused as exc:
        payload, status = {'code': exc.code}, 1
    except LLMToolError as exc:
        code = getattr(exc, 'incident_code', None)
        if code is None:
            raise
        payload, status = {'code': code}, 1
    if status == 1:
        payload = dict(payload, refused=True, incident_id=getattr(args, 'incident', None))
    print(json.dumps(payload, sort_keys=True, indent=2 if action == 'show' else None))
    if status:
        print(f'llmtool: {payload["code"]}', file=sys.stderr)
    return status
