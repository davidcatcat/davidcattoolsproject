"""Fail-closed Owner worker dispatch policy and TaskStore audit helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .errors import LLMToolError

POLICY_SCHEMA_VERSION = 1
MAX_POLICY_BYTES = 64 * 1024
MAX_AUDIT_ENTRIES = 64
WORKERS = frozenset((
    'codex-developer', 'claude-developer', 'opencode-developer', 'agy',
    'company-gw', 'claude-reviewer',
))
DISPATCH_STATES = frozenset(('ALLOWED', 'PAUSED', 'OWNER_ASSIGNMENT_ONLY'))
AUDIT_ACTIONS = frozenset((
    'DISPATCH', 'RETRY', 'REASSIGN', 'REVIEWER_HANDOFF', 'REVIEWER_RETRY',
))
_POLICY_PATH = Path(__file__).parent.parent / 'worker-dispatch.json'


class WorkerDispatchDenied(LLMToolError):
    """A worker dispatch was denied or could not be durably audited."""

    def __init__(self, code):
        super().__init__('worker dispatch denied; details omitted')
        self.dispatch_code = code


@dataclass(frozen=True)
class PolicyDecision:
    worker_key: str
    allowed: bool
    reason_code: str
    policy_sha256: str | None


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _parse_policy(raw):
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if (type(value) is not dict or set(value) != {'schema_version', 'workers'} or
            type(value.get('schema_version')) is not int or
            value['schema_version'] != POLICY_SCHEMA_VERSION or
            type(value.get('workers')) is not dict or
            set(value['workers']) != WORKERS):
        raise ValueError('invalid policy shape')
    for key, worker in value['workers'].items():
        if (type(worker) is not dict or
                set(worker) != {'dispatch', 'decided_by', 'decided_at', 'reason', 'evidence'} or
                worker.get('dispatch') not in DISPATCH_STATES or
                worker.get('decided_by') != 'Owner' or
                type(worker.get('decided_at')) is not str or
                type(worker.get('reason')) is not str or
                not worker['reason'] or len(worker['reason']) > 512 or
                type(worker.get('evidence')) is not str or
                not worker['evidence'].startswith(
                    'https://github.com/davidcatcat/davidcattoolsproject/issues/289')):
            raise ValueError('invalid worker policy')
        if date.fromisoformat(worker['decided_at']).isoformat() != worker['decided_at']:
            raise ValueError('invalid decision date')
    return value


def evaluate(worker_key):
    """Read the policy file from disk on every call; never cache decisions."""
    if type(worker_key) is not str or re.fullmatch(r'[a-z][a-z0-9-]{0,63}', worker_key) is None:
        key = 'unknown'
        return PolicyDecision(key, False, 'WORKER_KEY_INVALID', None)
    digest = None
    path = _POLICY_PATH
    try:
        before_path = path.lstat()
        if stat.S_ISLNK(before_path.st_mode):
            return PolicyDecision(worker_key, False, 'WORKER_POLICY_INVALID', None)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    before.st_size < 2 or before.st_size > MAX_POLICY_BYTES):
                return PolicyDecision(worker_key, False, 'WORKER_POLICY_INVALID', None)
            chunks = []
            remaining = MAX_POLICY_BYTES + 1
            while remaining:
                chunk = os.read(fd, min(4096, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b''.join(chunks)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        after_path = path.lstat()
        identity = lambda info: (info.st_dev, info.st_ino, info.st_mode,
                                 info.st_nlink, info.st_size, info.st_mtime_ns,
                                 info.st_ctime_ns)
        if (len(raw) > MAX_POLICY_BYTES or identity(before) != identity(after) or
                (before.st_dev, before.st_ino) != (after_path.st_dev, after_path.st_ino)):
            return PolicyDecision(worker_key, False, 'WORKER_POLICY_CHANGED', None)
        digest = hashlib.sha256(raw).hexdigest()
        policy = _parse_policy(raw)
    except FileNotFoundError:
        return PolicyDecision(worker_key, False, 'WORKER_POLICY_MISSING', None)
    except (OSError, UnicodeError, ValueError, TypeError):
        return PolicyDecision(worker_key, False, 'WORKER_POLICY_INVALID', digest)

    if worker_key not in WORKERS:
        return PolicyDecision(worker_key, False, 'WORKER_UNKNOWN', digest)
    state = policy['workers'][worker_key]['dispatch']
    if state == 'ALLOWED':
        return PolicyDecision(worker_key, True, 'OWNER_POLICY_ALLOWED', digest)
    if state == 'OWNER_ASSIGNMENT_ONLY':
        return PolicyDecision(worker_key, False, 'OWNER_ASSIGNMENT_REQUIRED', digest)
    return PolicyDecision(worker_key, False, 'OWNER_PAUSED', digest)


def audit_entry(decision, action):
    if action not in AUDIT_ACTIONS:
        action = 'DISPATCH'
    return {
        'attempt_id': uuid.uuid4().hex,
        'worker_key': decision.worker_key,
        'action': action,
        'decision': 'ALLOWED' if decision.allowed else 'BLOCKED',
        'reason_code': decision.reason_code,
        'policy_sha256': decision.policy_sha256,
        'at': datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
    }


def authorize_dispatch(store, task_id, worker_key, action='DISPATCH'):
    """Authorize and durably record one adapter-level dispatch attempt."""
    if store is None or type(task_id) is not str or not task_id:
        raise WorkerDispatchDenied('WORKER_DISPATCH_AUDIT_UNAVAILABLE')
    decision = evaluate(worker_key)
    try:
        current = store.get(task_id)
    except Exception:
        raise WorkerDispatchDenied('WORKER_DISPATCH_AUDIT_UNAVAILABLE') from None
    state_code = _adapter_state_code(current, worker_key)
    if state_code is not None:
        decision = PolicyDecision(worker_key, False, state_code, decision.policy_sha256)
    entry = audit_entry(decision, action)
    try:
        append = getattr(store, 'append_dispatch_audit')
        append(task_id, entry, expected={
            'developer': current.get('developer'),
            'dispatch_audit': current.get('dispatch_audit'),
        })
    except Exception:
        raise WorkerDispatchDenied('WORKER_DISPATCH_AUDIT_UNAVAILABLE') from None
    if not decision.allowed:
        raise WorkerDispatchDenied(decision.reason_code)
    return decision


def _adapter_state_code(record, worker_key):
    """Require the durable TaskStore claim to be in the adapter's dispatch phase."""
    if type(record) is not dict:
        return 'WORKER_ASSIGNMENT_NOT_DISPATCHING'
    developer = record.get('developer')
    if type(developer) is not dict:
        return 'WORKER_ASSIGNMENT_NOT_DISPATCHING'
    if worker_key == 'claude-reviewer':
        callback = developer.get('direct_result_callback')
        handoff = callback.get('review_handoff') if type(callback) is dict else None
        if type(handoff) is dict and handoff.get('dispatch_state') == 'DISPATCHING':
            return None
        return 'WORKER_ASSIGNMENT_NOT_DISPATCHING'
    if (developer.get('state') != 'DEVELOPER_DISPATCHING' or
            worker_key_for(developer.get('provider_profile')) != worker_key):
        return 'WORKER_ASSIGNMENT_NOT_DISPATCHING'
    return None


def task_store_attempt(current, changes):
    """Return the worker/action for a protected TaskStore state transition."""
    old_developer = current.get('developer')
    new_developer = changes.get('developer', old_developer)
    if type(new_developer) is not dict:
        return None

    old_callback = (old_developer.get('direct_result_callback')
                    if type(old_developer) is dict else None)
    new_callback = new_developer.get('direct_result_callback')
    old_handoff = (old_callback.get('review_handoff')
                   if type(old_callback) is dict else None)
    new_handoff = (new_callback.get('review_handoff')
                   if type(new_callback) is dict else None)
    if (type(new_handoff) is dict and new_handoff.get('dispatch_state') == 'DISPATCHING' and
            (type(old_handoff) is not dict or old_handoff.get('dispatch_state') != 'DISPATCHING')):
        previous = current.get('dispatch_audit', [])
        action = ('REVIEWER_RETRY' if any(
            type(entry) is dict and entry.get('worker_key') == 'claude-reviewer'
            for entry in previous) else 'REVIEWER_HANDOFF')
        return 'claude-reviewer', action

    if type(old_developer) is not dict:
        worker = worker_key_for(new_developer.get('provider_profile'))
        return (worker, 'DISPATCH') if worker is not None else ('unknown', 'DISPATCH')

    if (new_developer.get('assignment_id') != old_developer.get('assignment_id') or
            new_developer.get('provider_profile') != old_developer.get('provider_profile')):
        worker = worker_key_for(new_developer.get('provider_profile'))
        return (worker, 'REASSIGN') if worker is not None else ('unknown', 'REASSIGN')

    if (new_developer.get('state') == 'DEVELOPER_PENDING' and
            old_developer.get('state') != 'DEVELOPER_PENDING'):
        worker = worker_key_for(new_developer.get('provider_profile'))
        return (worker, 'RETRY') if worker is not None else ('unknown', 'RETRY')

    if (new_developer.get('state') == 'DEVELOPER_DISPATCHING' and
            old_developer.get('state') != 'DEVELOPER_DISPATCHING'):
        worker = worker_key_for(new_developer.get('provider_profile'))
        action = ('RETRY' if old_developer.get('state') != 'DEVELOPER_PENDING'
                  else 'DISPATCH')
        return (worker, action) if worker is not None else ('unknown', action)
    return None


def worker_key_for(provider_profile):
    if provider_profile == 'company-gw':
        return 'company-gw'
    if provider_profile == 'opencode':
        return 'opencode-developer'
    if provider_profile == 'claude-reviewer':
        return 'claude-reviewer'
    if provider_profile == 'codex':
        return 'codex-developer'
    if provider_profile == 'claude':
        return 'claude-developer'
    return None


def authorize_task_store_change(current, changes):
    """Atomically add the policy result to a TaskStore record and deny on failure."""
    attempt = task_store_attempt(current, changes)
    if attempt is None:
        return None
    worker_key, action = attempt
    decision = evaluate(worker_key)
    old_developer = current.get('developer')
    if action == 'RETRY' and type(old_developer) is dict and old_developer.get('state') not in (
            'DEVELOPER_UNAVAILABLE',):
        decision = PolicyDecision(worker_key, False, 'WORKER_REPLAY_FORBIDDEN',
                                  decision.policy_sha256)
    entry = audit_entry(decision, action)
    audit = list(current.get('dispatch_audit', []))
    if len(audit) >= MAX_AUDIT_ENTRIES:
        raise WorkerDispatchDenied('WORKER_DISPATCH_AUDIT_LIMIT')
    audit.append(entry)
    current['dispatch_audit'] = audit
    if not decision.allowed:
        return WorkerDispatchDenied(decision.reason_code)
    return None
