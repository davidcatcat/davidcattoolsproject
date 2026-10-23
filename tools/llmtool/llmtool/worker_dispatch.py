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
import sysconfig
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
    'DISPATCH', 'RETRY', 'REASSIGN', 'RECOVERY', 'REPAIR',
    'REVIEWER_HANDOFF', 'REVIEWER_RETRY',
))
def _policy_candidates():
    """List policy locations for this actual module layout, in authority order."""
    module = Path(__file__).resolve()
    source_policy = module.parent.parent / 'worker-dispatch.json'
    package_policy = module.parent / 'worker-dispatch.json'
    installed_policy = Path(sysconfig.get_path('data')) / 'llmtool' / 'worker-dispatch.json'
    result = []
    for candidate in (source_policy, package_policy, installed_policy):
        if os.path.lexists(candidate):
            absolute = Path(os.path.abspath(candidate))
            if absolute not in result:
                result.append(absolute)
    return result


def _policy_path():
    """Choose the canonical source file or the installed data file for this layout."""
    module = Path(__file__).resolve()
    source_policy = module.parent.parent / 'worker-dispatch.json'
    package_policy = module.parent / 'worker-dispatch.json'
    installed_policy = Path(sysconfig.get_path('data')) / 'llmtool' / 'worker-dispatch.json'
    for candidate in (source_policy, package_policy, installed_policy):
        if os.path.lexists(candidate):
            return Path(os.path.abspath(candidate))
    # Keep the path for the detected layout. A later missing-file check must fail
    # closed instead of falling back to a stale copy from another layout.
    return Path(os.path.abspath(source_policy))


_DEFAULT_POLICY_PATH = _policy_path()
_POLICY_PATH = _DEFAULT_POLICY_PATH
_OWNER_COMMENT_REF = re.compile(
    r'https://github\.com/davidcatcat/davidcattoolsproject/issues/289'
    r'#issuecomment-[1-9][0-9]*\Z')
_TASK_ID = re.compile(r'[0-9a-f]{32}\Z')
_POLICY_EVIDENCE_REF = re.compile(
    r'https://github\.com/davidcatcat/davidcattoolsproject/(?:issues/289|pull/283)'
    r'(?:#issuecomment-[1-9][0-9]*)?\Z')


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
        # Keep the current Owner-authored v1 file shape. The Owner label is
        # provenance only; dispatch authority comes from the exact switch and,
        # where required, an exact task/ref row.
        assignments = worker.get('owner_assignments') if type(worker) is dict else None
        if type(worker) is dict and 'owner_assignments' not in worker:
            assignments = []
        if (type(worker) is not dict or
                set(worker) not in (
                    {'dispatch', 'decided_by', 'decided_at', 'reason', 'evidence'},
                    {'dispatch', 'decided_by', 'decided_at', 'reason', 'evidence',
                     'owner_assignments'},
                ) or
                worker.get('decided_by') != 'Owner' or
                worker.get('dispatch') not in DISPATCH_STATES or
                type(worker.get('decided_at')) is not str or
                type(worker.get('reason')) is not str or
                not worker['reason'] or len(worker['reason']) > 512 or
                type(worker.get('evidence')) is not str or
                _POLICY_EVIDENCE_REF.fullmatch(worker['evidence']) is None or
                type(assignments) is not list or
                any(type(assignment) is not dict or
                    set(assignment) != {'task_id', 'owner_assignment_ref'} or
                    type(assignment.get('task_id')) is not str or
                    _TASK_ID.fullmatch(assignment['task_id']) is None or
                    type(assignment.get('owner_assignment_ref')) is not str or
                    _OWNER_COMMENT_REF.fullmatch(assignment['owner_assignment_ref']) is None
                    for assignment in assignments) or
                len({(assignment['task_id'], assignment['owner_assignment_ref'])
                     for assignment in assignments}) != len(assignments)):
            raise ValueError('invalid worker policy')
        if date.fromisoformat(worker['decided_at']).isoformat() != worker['decided_at']:
            raise ValueError('invalid decision date')
    return value


def _read_policy_file(path):
    before_path = path.lstat()
    if stat.S_ISLNK(before_path.st_mode):
        raise ValueError('policy symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size < 2 or before.st_size > MAX_POLICY_BYTES):
            raise ValueError('invalid policy file')
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
        raise RuntimeError('policy changed')
    return raw


def evaluate(worker_key, owner_assignment_ref=None, task_id=None):
    """Read the one policy for the detected layout on every call; never cache decisions."""
    if type(worker_key) is not str or re.fullmatch(r'[a-z][a-z0-9-]{0,63}', worker_key) is None:
        key = 'unknown'
        return PolicyDecision(key, False, 'WORKER_KEY_INVALID', None)
    digest = None
    path = _POLICY_PATH
    try:
        raw = _read_policy_file(path)
        # Source/editable execution can also see an installed data-file copy. It
        # never overrides the canonical source policy; conflicting copies fail
        # closed. An isolated wheel install has only the sys.prefix data-file.
        if path == _DEFAULT_POLICY_PATH:
            for candidate in _policy_candidates():
                if candidate != path and _read_policy_file(candidate) != raw:
                    return PolicyDecision(worker_key, False,
                                          'WORKER_POLICY_AMBIGUOUS', None)
        digest = hashlib.sha256(raw).hexdigest()
        policy = _parse_policy(raw)
    except FileNotFoundError:
        return PolicyDecision(worker_key, False, 'WORKER_POLICY_MISSING', None)
    except RuntimeError:
        return PolicyDecision(worker_key, False, 'WORKER_POLICY_CHANGED', None)
    except (OSError, UnicodeError, ValueError, TypeError):
        return PolicyDecision(worker_key, False, 'WORKER_POLICY_INVALID', digest)

    if worker_key not in WORKERS:
        return PolicyDecision(worker_key, False, 'WORKER_UNKNOWN', digest)
    state = policy['workers'][worker_key]['dispatch']
    if state == 'ALLOWED':
        return PolicyDecision(worker_key, True, 'OWNER_POLICY_ALLOWED', digest)
    if state == 'OWNER_ASSIGNMENT_ONLY':
        assignments = policy['workers'][worker_key].get('owner_assignments', [])
        if (type(task_id) is str and _TASK_ID.fullmatch(task_id) is not None and
                type(owner_assignment_ref) is str and
                _OWNER_COMMENT_REF.fullmatch(owner_assignment_ref) is not None and
                {'task_id': task_id, 'owner_assignment_ref': owner_assignment_ref} in assignments):
            return PolicyDecision(worker_key, True, 'OWNER_ASSIGNMENT_MATCHED', digest)
        return PolicyDecision(worker_key, False, 'OWNER_ASSIGNMENT_REQUIRED', digest)
    return PolicyDecision(worker_key, False, 'OWNER_PAUSED', digest)


def audit_entry(decision, action):
    if type(action) is not str or action not in AUDIT_ACTIONS:
        raise WorkerDispatchDenied('WORKER_DISPATCH_ACTION_INVALID')
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
    """Authorize and durably record one adapter-boundary attempt.

    TaskStore state transitions separately record logical actions such as
    RETRY, RECOVERY, REPAIR and REASSIGN; this record is the final adapter gate.
    """
    if store is None or type(task_id) is not str or not task_id:
        raise WorkerDispatchDenied('WORKER_DISPATCH_AUDIT_UNAVAILABLE')
    try:
        current = store.get(task_id)
    except Exception:
        raise WorkerDispatchDenied('WORKER_DISPATCH_AUDIT_UNAVAILABLE') from None
    developer = current.get('developer') if type(current) is dict else None
    owner_assignment_ref = (developer.get('owner_assignment_ref')
                            if type(developer) is dict else None)
    decision = evaluate(worker_key, owner_assignment_ref, task_id)
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


def task_store_attempts(current, changes):
    """Return every gated worker/action touched by one TaskStore transition."""
    old_developer = current.get('developer')
    new_developer = changes.get('developer', old_developer)
    if type(new_developer) is not dict:
        return []

    attempts = []
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
        attempts.append(('claude-reviewer', action))

    if type(old_developer) is not dict:
        worker = worker_key_for(new_developer.get('provider_profile'))
        attempts.append((worker or 'unknown', 'DISPATCH'))
        return attempts

    if (new_developer.get('assignment_id') != old_developer.get('assignment_id') or
            new_developer.get('provider_profile') != old_developer.get('provider_profile')):
        worker = worker_key_for(new_developer.get('provider_profile'))
        old_state = old_developer.get('state')
        action = ('RECOVERY' if old_state == 'DEVELOPER_UNAVAILABLE'
                  else 'REPAIR' if old_state in (
                      'DEVELOPER_RESPONDED', 'DEVELOPER_OUTPUT_REJECTED') else 'REASSIGN')
        attempts.append((worker or 'unknown', action))
        return attempts

    if (new_developer.get('state') == 'DEVELOPER_PENDING' and
            old_developer.get('state') != 'DEVELOPER_PENDING'):
        worker = worker_key_for(new_developer.get('provider_profile'))
        action = ('RECOVERY' if old_developer.get('state') == 'DEVELOPER_UNAVAILABLE'
                  else 'REPAIR' if old_developer.get('state') in (
                      'DEVELOPER_RESPONDED', 'DEVELOPER_OUTPUT_REJECTED') else 'RETRY')
        attempts.append((worker or 'unknown', action))
        return attempts

    if (new_developer.get('state') == 'DEVELOPER_DISPATCHING' and
            old_developer.get('state') != 'DEVELOPER_DISPATCHING'):
        worker = worker_key_for(new_developer.get('provider_profile'))
        action = ('RECOVERY' if old_developer.get('state') == 'DEVELOPER_UNAVAILABLE'
                  else 'REPAIR' if old_developer.get('state') in (
                      'DEVELOPER_RESPONDED', 'DEVELOPER_OUTPUT_REJECTED')
                  else 'RETRY' if old_developer.get('state') != 'DEVELOPER_PENDING'
                  else 'DISPATCH')
        attempts.append((worker or 'unknown', action))
    return attempts


def task_store_attempt(current, changes):
    """Return the first worker/action for compatibility with single-action callers."""
    attempts = task_store_attempts(current, changes)
    return attempts[0] if attempts else None


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


def authorize_task_store_change(current, changes, expected=None):
    """Atomically audit every policy result before a TaskStore transition."""
    attempts = task_store_attempts(current, changes)
    if not attempts:
        return None
    old_developer = current.get('developer')
    new_developer = changes.get('developer', old_developer)
    owner_assignment_ref = (new_developer.get('owner_assignment_ref')
                            if type(new_developer) is dict else None)
    audit = list(current.get('dispatch_audit', []))
    if len(audit) + len(attempts) > MAX_AUDIT_ENTRIES:
        raise WorkerDispatchDenied('WORKER_DISPATCH_AUDIT_LIMIT')
    entries = []
    decisions = []
    for worker_key, action in attempts:
        decision = evaluate(worker_key, owner_assignment_ref, current.get('task_id'))
        if action in ('RETRY', 'RECOVERY', 'REPAIR') and type(old_developer) is dict:
            expected_old_state = {
                'RETRY': (),
                'RECOVERY': ('DEVELOPER_UNAVAILABLE',),
                'REPAIR': ('DEVELOPER_RESPONDED', 'DEVELOPER_OUTPUT_REJECTED'),
            }[action]
            if old_developer.get('state') not in expected_old_state:
                decision = PolicyDecision(worker_key, False, 'WORKER_REPLAY_FORBIDDEN',
                                          decision.policy_sha256)
            if action == 'REPAIR' and not _has_failed_review_repair(current):
                decision = PolicyDecision(worker_key, False,
                                          'WORKER_REPAIR_NOT_AUTHORIZED',
                                          decision.policy_sha256)
        if action == 'REASSIGN':
            bound = (type(expected) is dict and 'developer' in expected and
                     expected['developer'] == old_developer)
            if old_developer.get('state') != 'DEVELOPER_PENDING' or not bound:
                decision = PolicyDecision(worker_key, False,
                                          'WORKER_REASSIGN_PRECONDITION',
                                          decision.policy_sha256)
        entries.append(audit_entry(decision, action))
        decisions.append(decision)
    current['dispatch_audit'] = audit + entries
    for decision in decisions:
        if not decision.allowed:
            return WorkerDispatchDenied(decision.reason_code)
    return None


def _has_failed_review_repair(record):
    developer = record.get('developer') if type(record) is dict else None
    callback = developer.get('direct_result_callback') if type(developer) is dict else None
    returned = callback.get('review_return') if type(callback) is dict else None
    return (type(returned) is dict and returned.get('state') == 'RETURNED' and
            returned.get('next_step') == 'RETURN_TO_DEVELOPER_ONCE')
