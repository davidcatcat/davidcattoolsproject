"""Durable PM -> Developer assignment inbox with claim receipts (#367).

The PM records one ASSIGNED event per dispatch with ``task assign``; it is
written only after the worker-dispatch gate allows the named worker, and is
bound to that worker and a D6 task id. The Developer lists its pending
assignments with ``task assignments --worker <name>`` (optionally blocking) and
claims one with ``task claim <id>``. A claim succeeds once; it stores a receipt
(claimer session id, claimer account digest, UTC time) that is the PM's
delivery evidence. Assignments still unclaimed after their claim timeout
(default 2 minutes) are listed as UNCLAIMED in the PM's ``task inbox``.

Everything is offline: one locked, atomically replaced sidecar file in the
shared TaskStore root. Nothing here starts a Worker or calls a provider.
"""

import hashlib
import os
import time
from datetime import datetime, timedelta, timezone

from .errors import LLMToolError
from .task_store import PM_NATIVE_SESSION_ID

DEFAULT_CLAIM_TIMEOUT_SECONDS = 120
DEFAULT_WAIT_SECONDS = 540
MAX_WAIT_SECONDS = 3600
POLL_SECONDS = 2.0
_UTC_SECONDS = '%Y-%m-%dT%H:%M:%SZ'
_ACCOUNT_DOMAIN = b'llmtool-developer-claim-account-v1\0'


def _now(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)


def _stamp(now):
    return now.strftime(_UTC_SECONDS)


def claimer_account(environ=None):
    """Digest identifying the claimer's Claude account (its configuration directory).

    Only a domain-separated digest of the directory path is stored; nothing
    inside the directory is read.
    """
    env = os.environ if environ is None else environ
    base = env.get('CLAUDE_CONFIG_DIR') or (
        os.path.join(env['HOME'], '.claude') if env.get('HOME') else None)
    if base is None or not os.path.isabs(base):
        raise LLMToolError('claimer account directory is unavailable')
    path = os.path.normpath(base).encode('utf-8', 'surrogateescape')
    return hashlib.sha256(_ACCOUNT_DOMAIN + path).hexdigest()[:16]


def assign(store, *, worker, d6_task_id, pm_session_key, task_id=None,
           owner_assignment_ref=None, claim_timeout=DEFAULT_CLAIM_TIMEOUT_SECONDS,
           now=None):
    """Record one gated ASSIGNED event; a gate refusal raises and writes nothing."""
    return store.record_developer_assignment(
        worker=worker, d6_task_id=d6_task_id, pm_session_id=pm_session_key,
        task_id=task_id, owner_assignment_ref=owner_assignment_ref,
        assigned_at=_stamp(_now(now)), claim_timeout_seconds=claim_timeout)


def _overdue(item, now):
    assigned = datetime.strptime(item['assigned_at'], _UTC_SECONDS).replace(
        tzinfo=timezone.utc)
    return now - assigned >= timedelta(seconds=item['claim_timeout_seconds'])


def pending_assignments(store, worker, *, now=None):
    """Unclaimed assignments for ``worker``, oldest first (late ones still claimable)."""
    now = _now(now)
    return [dict(item, overdue=_overdue(item, now))
            for item in store.get_developer_assignments()
            if item['state'] == 'ASSIGNED' and item['worker'] == worker]


def wait_assignments(store, worker, *, wait=False, timeout=DEFAULT_WAIT_SECONDS,
                     poll=POLL_SECONDS, clock=time.monotonic, sleep=time.sleep, now=None):
    """List pending assignments; with ``wait`` sleep-poll until one exists or timeout."""
    if type(timeout) is not int or not 0 <= timeout <= MAX_WAIT_SECONDS:
        raise LLMToolError('assignments timeout must be 0 to %d seconds' % MAX_WAIT_SECONDS)
    deadline = clock() + timeout
    while True:
        pending = pending_assignments(store, worker, now=now)
        if pending or not wait or clock() >= deadline:
            return {'worker': worker, 'assignments': pending,
                    'timed_out': bool(wait and not pending)}
        sleep(max(0.0, min(poll, deadline - clock())))


def claim(store, assignment_id, *, claimer_session_id, worker=None, environ=None, now=None):
    """Claim once; the stored receipt is the PM's delivery evidence."""
    if (type(claimer_session_id) is not str or
            PM_NATIVE_SESSION_ID.fullmatch(claimer_session_id) is None):
        raise LLMToolError('claimer session must be a lowercase session UUID')
    return store.claim_developer_assignment(
        assignment_id, claimer_session_id=claimer_session_id,
        claimer_account=claimer_account(environ), claimed_at=_stamp(_now(now)),
        worker=worker)


def unclaimed_for_pm(store, pm_session_key, *, now=None):
    """Assignments by this PM still unclaimed past their claim timeout."""
    now = _now(now)
    return [dict(item, state='UNCLAIMED')
            for item in store.get_developer_assignments()
            if item['pm_session_id'] == pm_session_key and item['state'] == 'ASSIGNED'
            and _overdue(item, now)]
