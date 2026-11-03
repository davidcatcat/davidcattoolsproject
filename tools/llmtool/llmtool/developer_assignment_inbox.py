"""Durable PM -> Developer assignment inbox with claim receipts (#367 slice 1).

``task assign`` re-runs the worker-dispatch gate, reads the registered D6
task (it must be ACTIVE) and records one ASSIGNED event bound to that D6, its
root and its HEAD (``source_sha``), expiring within 15 minutes. The Developer
lists its live assignments with ``task assignments --worker <name>``
(optionally a bounded wait) and claims one with ``task claim``. Before the
one-shot ASSIGNED -> CLAIMED write the claim re-runs the dispatch gate
(recorded in the assignment audit), re-reads the D6 (still ACTIVE at
``source_sha``), checks the worker, D6 and expiry, and requires this
session's Claude Code transcript under the claimer's own ``CLAUDE_CONFIG_DIR``
for ``cwd`` equal to the D6 root (plain owned files, no symlinks). A late claim
moves the assignment to EXPIRED. The PM sees UNCLAIMED or CLAIMED (with the
receipt) in ``task inbox``.

The receipt is accountability and fail-closed bookkeeping, not
authentication: every account runs as the same OS user. A claim is never
test, review or completion evidence. Everything is offline; nothing here
starts a Worker, reads credentials or calls a provider.
"""

import hashlib
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

from .errors import LLMToolError
from .task_store import ASSIGNMENT_EXPIRY_MAX_SECONDS, PM_NATIVE_SESSION_ID

DEFAULT_EXPIRY_SECONDS = 600
DEFAULT_WAIT_SECONDS = 540
MAX_WAIT_SECONDS = 600
POLL_SECONDS = 2.0
_UTC_SECONDS = '%Y-%m-%dT%H:%M:%SZ'
_ACCOUNT_DOMAIN = b'llmtool-developer-claim-account-v1\0'


class AssignmentRefused(LLMToolError):
    """An assignment or claim was refused; ``assignment_code`` says why."""

    def __init__(self, code):
        super().__init__('developer assignment refused; details omitted')
        self.assignment_code = code


def refusal_code(exc):
    """The stable refusal code carried by an assignment/claim error, if any."""
    for name in ('assignment_code', 'dispatch_code', 'task_store_error_code'):
        code = getattr(exc, name, None)
        if code:
            return code
    return None


def _now(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)


def _stamp(now):
    return now.strftime(_UTC_SECONDS)


def _parse(stamp):
    return datetime.strptime(stamp, _UTC_SECONDS).replace(tzinfo=timezone.utc)


def account_dir_label(environ=None):
    """Digest labelling the claimer's Claude account (its configuration directory).

    Only a domain-separated digest of the directory path is stored; nothing
    inside the directory is read.
    """
    env = os.environ if environ is None else environ
    base = env.get('CLAUDE_CONFIG_DIR') or (
        os.path.join(env['HOME'], '.claude') if env.get('HOME') else None)
    if base is None or not os.path.isabs(base):
        raise AssignmentRefused('ASSIGNMENT_ACCOUNT_DIR_UNAVAILABLE')
    path = os.path.normpath(base).encode('utf-8', 'surrogateescape')
    return hashlib.sha256(_ACCOUNT_DOMAIN + path).hexdigest()[:16]


def discover_common_dir(cwd=None):
    """The absolute, resolved Git common directory of ``cwd`` (D6 registry home)."""
    try:
        output = subprocess.run(
            ['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'],
            cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=True,
        ).stdout.decode('utf-8').strip()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise AssignmentRefused('ASSIGNMENT_COMMON_DIR_UNAVAILABLE') from None
    common_dir = os.path.realpath(output)
    if not output or not common_dir.endswith('/.git'):
        raise AssignmentRefused('ASSIGNMENT_COMMON_DIR_UNAVAILABLE')
    return common_dir


def _registration(d6_task_id, common_dir):
    from . import task_completion
    try:
        return task_completion.read_d6_registration(d6_task_id, common_dir)
    except LLMToolError:
        raise AssignmentRefused('ASSIGNMENT_D6_NOT_ACTIVE') from None


def _decision(worker, owner_assignment_ref, task_id):
    from . import worker_dispatch
    decision = worker_dispatch.evaluate(
        worker, owner_assignment_ref=owner_assignment_ref, task_id=task_id)
    entry = {'decision': 'ALLOWED' if decision.allowed else 'BLOCKED',
             'reason_code': decision.reason_code, 'policy_sha256': decision.policy_sha256}
    denied = None if decision.allowed else worker_dispatch.WorkerDispatchDenied(
        decision.reason_code)
    return entry, denied


def assign(store, *, worker, d6_task_id, common_dir, pm_session_key, task_id=None,
           owner_assignment_ref=None, expires_in=DEFAULT_EXPIRY_SECONDS, now=None):
    """Record one gated ASSIGNED event bound to an ACTIVE D6; a refusal writes nothing."""
    if (type(expires_in) is not int or
            not 0 < expires_in <= ASSIGNMENT_EXPIRY_MAX_SECONDS):
        raise AssignmentRefused('ASSIGNMENT_EXPIRY_INVALID')
    assigned = _now(now)

    def gate():
        entry, denied = _decision(worker, owner_assignment_ref, task_id)
        if denied is not None:
            raise denied
        registration = _registration(d6_task_id, common_dir)
        return {'dispatch': entry, 'd6_root': registration.root,
                'source_sha': registration.result_sha}

    return store.record_developer_assignment(
        worker=worker, d6_task_id=d6_task_id, common_dir=common_dir,
        pm_session_id=pm_session_key, task_id=task_id,
        owner_assignment_ref=owner_assignment_ref, assigned_at=_stamp(assigned),
        expires_at=_stamp(assigned + timedelta(seconds=expires_in)), gate=gate)


def claim(store, assignment_id, *, worker, d6_task_id, session_id, environ=None, now=None):
    """One-shot claim after re-running the dispatch, D6 and transcript checks."""
    if type(session_id) is not str or PM_NATIVE_SESSION_ID.fullmatch(session_id) is None:
        raise AssignmentRefused('ASSIGNMENT_SESSION_INVALID')
    label = account_dir_label(environ)

    def gate(item):
        entry, denied = _decision(item['worker'], item['owner_assignment_ref'],
                                  item['task_id'])
        if denied is not None:
            return entry, denied
        try:
            registration = _registration(item['d6_task_id'], item['common_dir'])
        except AssignmentRefused as error:
            return entry, error
        if (registration.result_sha != item['source_sha'] or
                registration.root != item['d6_root']):
            return entry, AssignmentRefused('ASSIGNMENT_SOURCE_STALE')
        from . import active_pm_inbox
        try:
            active_pm_inbox.observe_claude_session(session_id, item['d6_root'], environ)
        except LLMToolError as error:
            if getattr(error, 'role_config_code', None):
                raise
            return entry, AssignmentRefused('ASSIGNMENT_SESSION_NOT_FOUND')
        return entry, None

    return store.claim_developer_assignment(
        assignment_id, worker=worker, d6_task_id=d6_task_id, session_id=session_id,
        account_dir_label=label, clock=lambda: _stamp(_now(now)), gate=gate)


def _live(item, now):
    return item['state'] == 'ASSIGNED' and now < _parse(item['expires_at'])


def pending_assignments(store, worker, *, now=None):
    """This worker's live (unexpired, unclaimed) assignments, oldest first; ids only."""
    now = _now(now)
    return [{key: item[key] for key in ('assignment_id', 'd6_task_id', 'task_id',
                                        'source_sha', 'expires_at')}
            for item in store.get_developer_assignments()
            if item['worker'] == worker and _live(item, now)]


def wait_assignments(store, worker, *, wait=False, timeout=DEFAULT_WAIT_SECONDS,
                     poll=POLL_SECONDS, clock=time.monotonic, sleep=time.sleep, now=None):
    """List live assignments; with ``wait`` sleep-poll until one exists or the bound passes."""
    if type(timeout) is not int or not 0 <= timeout <= MAX_WAIT_SECONDS:
        raise AssignmentRefused('ASSIGNMENT_WAIT_TIMEOUT_INVALID')
    deadline = clock() + timeout
    while True:
        pending = pending_assignments(store, worker, now=now)
        if pending or not wait or clock() >= deadline:
            return {'worker': worker, 'assignments': pending,
                    'timed_out': bool(wait and not pending)}
        sleep(max(0.0, min(poll, deadline - clock())))


def pm_assignments(store, pm_session_key, *, now=None):
    """This PM's assignments as (unclaimed, claimed) views for ``task inbox``.

    UNCLAIMED: expired without a claim. CLAIMED: carries the stored receipt.
    """
    now = _now(now)
    unclaimed, claimed = [], []
    for item in store.get_developer_assignments():
        if item['pm_session_id'] != pm_session_key:
            continue
        view = {key: item[key] for key in ('assignment_id', 'worker', 'd6_task_id',
                                           'task_id', 'source_sha', 'expires_at')}
        if item['state'] == 'CLAIMED':
            claimed.append(dict(view, state='CLAIMED', claim=item['claim']))
        elif item['state'] == 'EXPIRED' or not _live(item, now):
            unclaimed.append(dict(view, state='UNCLAIMED'))
    return unclaimed, claimed


def unclaimed_for_pm(store, pm_session_key, *, now=None):
    """Assignments by this PM that expired without a claim."""
    return pm_assignments(store, pm_session_key, now=now)[0]
