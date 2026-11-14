"""Developer quota availability (#1816): a technical observation, never authority.

The Owner switch in worker-dispatch.json decides first and is never written
here. When it allows a worker, a fresh quota FAIL in the TaskStore worker
observations (layer ``quota``) refuses the dispatch with
WORKER_QUOTA_UNAVAILABLE before any Task or D6 write. An absent or expired
observation reads UNKNOWN: it is never PASS and never blocks.

Only a Developer quota code (``..._QUOTA_EXHAUSTED``, the gw-agent quota code
with its gateway suffix) or a route rate-limit code writes FAIL. A run with
evidence of a provider result writes PASS. Test failures, program errors,
timeouts and refusals write nothing. No model switch, no paid route.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from . import worker_dispatch
from .errors import LLMToolError
from .task_store import (WORKER_AVAILABILITY_MAX_AGE_DAYS, WORKER_KEYS,
                         WORKER_OBSERVATION_STATUSES)

QUOTA_LAYER = 'quota'
QUOTA_FRESH_HOURS = 6
QUOTA_REFUSAL_CODE = 'WORKER_QUOTA_UNAVAILABLE'
# An unreadable observation file fails closed, after the Owner switch.
QUOTA_UNREADABLE_CODE = 'WORKER_QUOTA_UNREADABLE'
RESET_SOURCE = 'PROVIDER_REPORTED'
_TIME_FORMAT = '%Y-%m-%dT%H:%M:%SZ'

_QUOTA_CODE = re.compile(r'[A-Z][A-Z0-9_]*_QUOTA_EXHAUSTED\Z')
# gw-agent appends the gateway code to its quota code, e.g. ..._HTTP_429.
_GATEWAY_QUOTA_CODE = re.compile(r'[A-Z0-9_]*GW_AGENT_QUOTA_EXHAUSTED_[A-Z0-9_]{1,48}\Z')
_RATE_LIMIT_CODE = re.compile(r'[A-Z][A-Z0-9_]*_(?:RATE_LIMITED|HTTP_429)\Z')
# States that hold a provider result (a response, or output the host rejected).
_PROVIDER_RESULT_STATES = frozenset(('DEVELOPER_RESPONDED', 'DEVELOPER_OUTPUT_REJECTED'))
_SHA256_HEX = re.compile(r'[0-9a-f]{64}\Z')
_UNREAD = object()


@dataclass(frozen=True)
class QuotaGate:
    worker_key: str | None
    refused: bool
    reason_code: str | None
    quota: dict = field(default_factory=dict)
    dispatch_decision: str | None = None
    dispatch_reason_code: str | None = None


def is_quota_code(code):
    """True only for a Developer quota code or a route rate-limit code."""
    return type(code) is str and (
        _QUOTA_CODE.fullmatch(code) is not None or
        _GATEWAY_QUOTA_CODE.fullmatch(code) is not None or
        _RATE_LIMIT_CODE.fullmatch(code) is not None)


def classify_outcome(developer):
    """Return 'FAIL', 'PASS' or None (nothing to record) for one Developer record."""
    if type(developer) is not dict:
        return None
    if is_quota_code(developer.get('error_code')):
        return 'FAIL'
    if developer.get('state') in _PROVIDER_RESULT_STATES:
        return 'PASS'
    execution = developer.get('execution')
    if type(execution) is not dict or type(execution.get('process_exit_code')) is not int:
        return None
    if execution.get('status') == 'DONE':
        # The provider process finished and reported: a non-quota outcome.
        return 'PASS'
    if execution.get('status') == 'BLOCKED' and _evidenced_response(developer, execution):
        # The provider returned and the host then blocked the result (e.g. a
        # Claude run with no changes, or a Codex run that reported BLOCKED).
        return 'PASS'
    return None


def _evidenced_response(developer, execution):
    """A BLOCKED run counts only with a returned session and output digest.

    A prelaunch refusal has no process evidence; a timeout or interruption
    never counts, whatever its execution fields say.
    """
    code = developer.get('error_code')
    if type(code) is str and ('TIMEOUT' in code or 'INTERRUPTED' in code):
        return False
    session = execution.get('native_session_id')
    digest = execution.get('stdout_sha256')
    return (type(session) is str and session != '' and type(digest) is str and
            _SHA256_HEX.fullmatch(digest) is not None)


def worker_key_for_provider(provider_profile):
    """The quota worker key for a Developer route, or None when it has none."""
    worker = worker_dispatch.worker_key_for(provider_profile)
    if worker is None or worker == 'claude-reviewer' or worker not in WORKER_KEYS:
        return None
    return worker


def _now(now=None):
    value = now or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _stamp(value):
    return value.strftime(_TIME_FORMAT)


def _trusted_reset(reset_at, now):
    """A provider reset time is trusted when exact, in the future and in bounds."""
    if type(reset_at) is not str:
        return None
    try:
        parsed = datetime.strptime(reset_at, _TIME_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if (_stamp(parsed) != reset_at or parsed <= now or
            parsed - now > timedelta(days=WORKER_AVAILABILITY_MAX_AGE_DAYS)):
        return None
    return parsed


def _evidence_sha256(task_id, developer, status):
    value = {
        'task_id': task_id if type(task_id) is str else None,
        'assignment_id': developer.get('assignment_id'),
        'state': developer.get('state'),
        'error_code': developer.get('error_code'),
        'status': status,
    }
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
                     default=lambda _: None)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def record_outcome(store, worker_key, developer, *, task_id=None, reset_at=None, now=None):
    """Write the quota observation for one Developer outcome; return its status or None.

    FAIL is fresh until a trusted provider reset_at, else observed_at + 6 h.
    PASS is fresh for 6 h. Nothing is written when the outcome is not evidence.
    """
    status = classify_outcome(developer)
    if status is None or type(worker_key) is not str or worker_key not in WORKER_KEYS:
        return None
    observed = _now(now)
    fresh_until = observed + timedelta(hours=QUOTA_FRESH_HOURS)
    reset = {}
    trusted = _trusted_reset(reset_at, observed) if status == 'FAIL' else None
    if trusted is not None:
        fresh_until = trusted
        reset = {'reset_at': _stamp(trusted), 'reset_source': RESET_SOURCE}
    store.record_worker_observation(
        worker_key=worker_key, layer=QUOTA_LAYER, status=status,
        observed_at=_stamp(observed), fresh_until=_stamp(fresh_until),
        source='LOCAL_OBSERVATION',
        evidence_sha256=_evidence_sha256(task_id, developer, status), **reset)
    return status


def developer_snapshot(store, task_id):
    """Copy a Task's Developer record before a run; unreadable means do not record."""
    try:
        record = store.get(task_id)
    except Exception:
        return _UNREAD
    if type(record) is not dict:
        return _UNREAD
    return copy.deepcopy(record.get('developer'))


def record_task_outcome(store, task_id, before, *, reset_at=None):
    """Record the quota outcome of the run that changed this Task's Developer record.

    Best effort: an unchanged record (refusal before any write) or any read or
    write error records nothing, which reads UNKNOWN and never blocks.
    """
    if before is _UNREAD:
        return None
    try:
        record = store.get(task_id)
        developer = record.get('developer') if type(record) is dict else None
        if type(developer) is not dict or developer == before:
            return None
        worker = worker_key_for_provider(developer.get('provider_profile'))
        if worker is None:
            return None
        return record_outcome(store, worker, developer, task_id=task_id,
                              reset_at=reset_at)
    except Exception:
        return None


def _unknown():
    return {'status': 'UNKNOWN', 'observed_at': None, 'fresh_until': None}


def quota_status(store, worker_key):
    """Return this worker's quota observation; absent or expired reads UNKNOWN.

    A store without observations reads UNKNOWN. An unreadable observation
    file raises the store's LLMToolError.
    """
    getter = getattr(store, 'get_worker_observations', None)
    if getter is None:
        return _unknown()
    view = getter()
    worker = view.get(worker_key) if type(view) is dict else None
    entry = worker.get(QUOTA_LAYER) if type(worker) is dict else None
    if type(entry) is not dict or entry.get('status') not in WORKER_OBSERVATION_STATUSES:
        return _unknown()
    result = {'status': entry['status'], 'observed_at': entry.get('observed_at'),
              'fresh_until': entry.get('fresh_until')}
    # A reset time is shown only when the provider reported it.
    if (entry['status'] == 'FAIL' and entry.get('reset_source') == RESET_SOURCE and
            type(entry.get('reset_at')) is str):
        result.update(reset_at=entry['reset_at'], reset_source=RESET_SOURCE)
    return result


def quota_gate(store, worker_key, *, owner_assignment_ref=None, policy_task_id=None):
    """Decide whether a fresh quota FAIL refuses this dispatch.

    The Owner switch decides first: when worker_dispatch refuses, this gate
    does not refuse, so the caller's own policy refusal stands unchanged. It
    is read only on a FAIL or an unreadable file; it is never written.
    """
    if type(worker_key) is not str or worker_key not in WORKER_KEYS:
        return QuotaGate(worker_key, False, None, _unknown())
    try:
        quota = quota_status(store, worker_key)
        reason = QUOTA_REFUSAL_CODE if quota['status'] == 'FAIL' else None
    except LLMToolError:
        quota, reason = _unknown(), QUOTA_UNREADABLE_CODE
    if reason is None:
        return QuotaGate(worker_key, False, None, quota)
    decision = worker_dispatch.evaluate(worker_key, owner_assignment_ref, policy_task_id)
    if not decision.allowed:
        return QuotaGate(worker_key, False, None, quota, 'BLOCKED', decision.reason_code)
    return QuotaGate(worker_key, True, reason, quota, 'ALLOWED', decision.reason_code)


def refusal_view(task_id, gate):
    """The printed refusal: no Task, D6 or policy write happened."""
    return {
        'task_id': task_id, 'state': 'DISPATCH_REFUSED', 'error_code': gate.reason_code,
        'worker_key': gate.worker_key,
        'worker_dispatch': {'decision': gate.dispatch_decision,
                            'reason_code': gate.dispatch_reason_code},
        'quota': dict(gate.quota),
    }
