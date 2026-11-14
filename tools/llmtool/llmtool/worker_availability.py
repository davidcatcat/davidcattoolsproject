"""#1816 Developer quota availability gate.

A technical gate only. It reads and writes the quota layer of the TaskStore
worker observations and never reads, writes or overrides worker-dispatch.json:
the Owner dispatch policy stays the only dispatch authority, is checked first,
and is never changed here. No model switch, no paid route, no new permission.

* Only a Developer ``error_code`` ending ``_QUOTA_EXHAUSTED`` (or a route
  rate-limit code ending ``_RATE_LIMITED``) records quota FAIL. Test failures,
  program errors, timeouts and refusals never do.
* A run that returned a provider result records quota PASS.
* FAIL is fresh until a trusted provider-reported reset time, else for six
  hours from the observation. An expired or absent record reads UNKNOWN: never
  PASS, and it does not block.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from . import worker_dispatch
from .errors import LLMToolError
from .task_store import (WORKER_AVAILABILITY_MAX_AGE_DAYS, WORKER_KEYS,
                         WORKER_QUOTA_LAYER, WORKER_QUOTA_RESET_SOURCES)

QUOTA_UNAVAILABLE_CODE = 'WORKER_QUOTA_UNAVAILABLE'
QUOTA_DEFAULT_FRESH = timedelta(hours=6)
QUOTA_EXHAUSTED_SUFFIX = '_QUOTA_EXHAUSTED'
RATE_LIMITED_SUFFIX = '_RATE_LIMITED'
PROVIDER_REPORTED = 'PROVIDER_REPORTED'
# Developer states that show the provider returned a result.
PROVIDER_RESULT_STATES = frozenset(('DEVELOPER_RESPONDED', 'DEVELOPER_OUTPUT_REJECTED'))
# Developer routes the delegate gate covers; each maps to its worker key.
_DELEGATE_ROUTES = frozenset(('claude', 'opencode', 'agy', 'codex', 'gw-agent'))
_CODE = re.compile(r'[A-Z0-9_]{1,64}\Z')
_STAMP = '%Y-%m-%dT%H:%M:%SZ'
_UNKNOWN = {'status': 'UNKNOWN', 'observed_at': None, 'fresh_until': None,
            'source': None, 'evidence_sha256': None}


class WorkerQuotaUnavailable(LLMToolError):
    """A fresh quota FAIL refused the dispatch before any Task or D6 write."""

    def __init__(self, worker_key, reset_at=None):
        text = f'{QUOTA_UNAVAILABLE_CODE}: {worker_key} has a fresh quota FAIL'
        text += (f'; provider-reported reset at {reset_at}' if reset_at is not None
                 else '; reset time UNKNOWN')
        super().__init__(text)
        self.code = QUOTA_UNAVAILABLE_CODE
        self.worker_key = worker_key
        self.reset_at = reset_at


def _stamp(value):
    return value.astimezone(timezone.utc).strftime(_STAMP)


def _parse_stamp(value):
    if type(value) is not str:
        return None
    try:
        parsed = datetime.strptime(value, _STAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed if _stamp(parsed) == value else None


def delegate_worker_key(provider):
    """Worker key for a Developer route the quota gate covers, else None."""
    if provider not in _DELEGATE_ROUTES:
        return None
    return worker_dispatch.worker_key_for(provider)


def quota_state(store, worker_key):
    """Read the quota-layer view for one worker: PASS, FAIL or UNKNOWN.

    A worker without an observation slot, or a store without the observation
    registry, has no quota evidence and reads UNKNOWN.
    """
    reader = getattr(store, 'get_worker_observations', None)
    if worker_key not in WORKER_KEYS or reader is None:
        return dict(_UNKNOWN)
    view = reader()
    return dict(view[worker_key][WORKER_QUOTA_LAYER])


def quota_gate(store, worker_key):
    """Refuse a dispatch only while a fresh quota FAIL is recorded.

    Read only: it writes no Task, no D6 and no policy. Absent or expired
    records read UNKNOWN and pass. When the Owner policy itself refuses the
    worker, that policy refusal is raised first, unchanged. Returns the quota
    view when the dispatch may go on.
    """
    item = quota_state(store, worker_key)
    if item.get('status') != 'FAIL':
        return item
    decision = worker_dispatch.evaluate(worker_key)
    # OWNER_ASSIGNMENT_REQUIRED needs the route's own Task/ref binding, so it is
    # not a refusal here; any other non-ALLOWED decision is the policy's own.
    if not decision.allowed and decision.reason_code != 'OWNER_ASSIGNMENT_REQUIRED':
        raise worker_dispatch.WorkerDispatchDenied(decision.reason_code)
    reset_at = (item.get('reset_at')
                if item.get('reset_source') in WORKER_QUOTA_RESET_SOURCES else None)
    raise WorkerQuotaUnavailable(worker_key, reset_at)


def classify_outcome(developer):
    """Return QUOTA_EXHAUSTED, RATE_LIMITED, PASS or None (record nothing)."""
    if type(developer) is not dict:
        return None
    code = developer.get('error_code')
    if type(code) is str and _CODE.fullmatch(code) is not None:
        if code.endswith(QUOTA_EXHAUSTED_SUFFIX):
            return 'QUOTA_EXHAUSTED'
        if code.endswith(RATE_LIMITED_SUFFIX):
            return 'RATE_LIMITED'
    if developer.get('state') in PROVIDER_RESULT_STATES:
        return 'PASS'
    # Timeouts, program errors, refusals and unknown outcomes say nothing
    # about quota.
    return None


def _trusted_reset(reset_at, observed):
    parsed = _parse_stamp(reset_at)
    if (parsed is None or parsed <= observed or
            parsed - observed > timedelta(days=WORKER_AVAILABILITY_MAX_AGE_DAYS)):
        return None
    return parsed


def record_outcome(store, worker_key, task, *, reset_at=None, now=None):
    """Record the quota outcome of one finished delegate on its Task record.

    ``reset_at`` is used only when the caller has it from the provider (a
    trusted, PROVIDER_REPORTED reset); otherwise FAIL is fresh for six hours.
    Returns the written status, or None when nothing was written.
    """
    if worker_key not in WORKER_KEYS or type(task) is not dict:
        return None
    developer = task.get('developer')
    outcome = classify_outcome(developer)
    if outcome is None:
        return None
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0)
    evidence = {
        'task_id': task.get('task_id'), 'worker_key': worker_key,
        'assignment_id': developer.get('assignment_id'),
        'state': developer.get('state'), 'error_code': developer.get('error_code'),
        'observed_at': _stamp(observed), 'outcome': outcome,
    }
    fields = {
        'worker_key': worker_key, 'layer': WORKER_QUOTA_LAYER,
        'observed_at': _stamp(observed), 'source': 'LOCAL_OBSERVATION',
        'evidence_sha256': hashlib.sha256(json.dumps(
            evidence, ensure_ascii=True, sort_keys=True,
            separators=(',', ':')).encode('utf-8')).hexdigest(),
    }
    if outcome == 'PASS':
        fields.update(status='PASS', fresh_until=_stamp(observed + QUOTA_DEFAULT_FRESH))
    else:
        trusted = _trusted_reset(reset_at, observed) if reset_at is not None else None
        fields.update(status='FAIL', condition=outcome)
        if trusted is None:
            fields['fresh_until'] = _stamp(observed + QUOTA_DEFAULT_FRESH)
        else:
            fields.update(fresh_until=_stamp(trusted), reset_at=_stamp(trusted),
                          reset_source=PROVIDER_REPORTED)
    store.record_worker_observation(**fields)
    return fields['status']


def developer_snapshot(store, task_id):
    """Copy of the Task's developer record before a delegate, or None."""
    try:
        task = store.get(task_id)
    except Exception:
        return None
    developer = task.get('developer') if type(task) is dict else None
    return json.loads(json.dumps(developer)) if type(developer) is dict else None


def record_delegate_outcome(store, worker_key, task_id, before):
    """Best-effort outcome record after a delegate returned or raised.

    Records only when the delegate changed the developer record, so a refused
    or replayed delegate never re-records an earlier outcome. The observation
    is evidence only; a failure to write it never changes the delegate result.
    """
    try:
        task = store.get(task_id)
        developer = task.get('developer') if type(task) is dict else None
        if type(developer) is not dict or developer == before:
            return None
        return record_outcome(store, worker_key, task)
    except Exception:
        return None
