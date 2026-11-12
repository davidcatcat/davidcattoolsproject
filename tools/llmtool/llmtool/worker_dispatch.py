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
from .config import repository_config

POLICY_SCHEMA_VERSION = 1
MAX_POLICY_BYTES = 64 * 1024
MAX_AUDIT_ENTRIES = 64
MAX_REPAIR_PER_ASSIGNMENT = 1
MAX_RECOVERY_PER_ASSIGNMENT = 1
WORKERS = frozenset((
    'codex-developer', 'claude-developer', 'opencode-developer', 'agy',
    'company-gw', 'company-gw-agent', 'claude-reviewer', 'codex-reviewer',
))
# #1502 WP-B: the AI Debug routes have their own keys. A policy file written
# before them (exactly WORKERS) still parses; a debug key it lacks is refused
# with WORKER_POLICY_ENTRY_MISSING. A file with only one debug key is invalid.
DEBUG_WORKERS = frozenset(('codex-debug', 'claude-debug'))
DISPATCH_STATES = frozenset(('ALLOWED', 'PAUSED', 'OWNER_ASSIGNMENT_ONLY'))
AUDIT_ACTIONS = frozenset((
    'DISPATCH', 'RETRY', 'REASSIGN', 'RECOVERY', 'REPAIR',
    'TIMEOUT_CONTINUATION', 'REVIEWER_HANDOFF', 'REVIEWER_RETRY',
    'COMMITTED_RESULT_CARRY',
))
MAX_TIMEOUT_CONTINUATIONS_PER_TASK = 1
MAX_COMMITTED_CARRIES = 1  # per Developer assignment (#1148)
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


def _build_reference_patterns(repository=None):
    """Build exact evidence references from validated trusted user config."""
    if repository is None:
        repository = repository_config()
    slug = re.escape(repository['slug'])
    dispatch_issue = repository['dispatch_issue']
    evidence_pr = repository['dispatch_evidence_pr']
    return (
        re.compile(r'https://github\.com/' + slug + r'/issues/' + str(dispatch_issue)
                   + r'#issuecomment-[1-9][0-9]*\Z'),
        re.compile(r'https://github\.com/' + slug + r'/(?:issues/' + str(dispatch_issue)
                   + r'|pull/' + str(evidence_pr)
                   + r')(?:#issuecomment-[1-9][0-9]*)?\Z'),
    )


def owner_comment_ref():
    """Return the current trust-bound owner assignment reference pattern."""
    return _build_reference_patterns()[0]


_TASK_ID = re.compile(r'[0-9a-f]{32}\Z')
_GW_AGENT_PAUSED_REF = (
    'https://github.com/davidcatcat/davidcattoolsproject/issues/120')
_GW_AGENT_OWNER_REF = (
    'https://github.com/davidcatcat/davidcattoolsproject/'
    'issues/120#issuecomment-6760670167')


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


def _parse_policy(raw, patterns=None):
    if patterns is None:
        patterns = _build_reference_patterns()
    owner_comment_ref, policy_evidence_ref = patterns
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if (type(value) is not dict or set(value) != {'schema_version', 'workers'} or
            type(value.get('schema_version')) is not int or
            value['schema_version'] != POLICY_SCHEMA_VERSION or
            type(value.get('workers')) is not dict or
            set(value['workers']) not in (WORKERS, WORKERS | DEBUG_WORKERS)):
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
                (worker.get('decided_by') != 'Owner' and not (
                    key == 'company-gw-agent' and worker.get('decided_by') == 'PM' and
                    worker.get('dispatch') == 'PAUSED' and
                    worker.get('evidence') ==
                    'https://github.com/davidcatcat/davidcattoolsproject/issues/120')) or
                worker.get('dispatch') not in DISPATCH_STATES or
                type(worker.get('decided_at')) is not str or
                type(worker.get('reason')) is not str or
                not worker['reason'] or len(worker['reason']) > 512 or
                type(worker.get('evidence')) is not str or
                (policy_evidence_ref.fullmatch(worker['evidence']) is None and not (
                    key == 'company-gw-agent' and (
                        (worker.get('dispatch') == 'PAUSED' and
                         worker.get('decided_by') == 'PM' and
                         worker['evidence'] == _GW_AGENT_PAUSED_REF) or
                        (worker.get('dispatch') == 'ALLOWED' and
                         worker.get('decided_by') == 'Owner' and
                         worker['evidence'] == _GW_AGENT_OWNER_REF)))) or
                type(assignments) is not list or
                any(type(assignment) is not dict or
                    set(assignment) != {'task_id', 'owner_assignment_ref'} or
                    type(assignment.get('task_id')) is not str or
                    _TASK_ID.fullmatch(assignment['task_id']) is None or
                    type(assignment.get('owner_assignment_ref')) is not str or
                    owner_comment_ref.fullmatch(assignment['owner_assignment_ref']) is None
                    for assignment in assignments) or
                len({(assignment['task_id'], assignment['owner_assignment_ref'])
                     for assignment in assignments}) != len(assignments)):
            raise ValueError('invalid worker policy')
        if key == 'company-gw-agent':
            is_pm_paused = (worker.get('dispatch') == 'PAUSED' and
                            worker.get('decided_by') == 'PM' and
                            worker.get('evidence') == _GW_AGENT_PAUSED_REF)
            is_owner_allowed = (worker.get('dispatch') == 'ALLOWED' and
                                worker.get('decided_by') == 'Owner' and
                                worker.get('evidence') == _GW_AGENT_OWNER_REF)
            if not (is_pm_paused or is_owner_allowed):
                raise ValueError('gw-agent requires the exact paused or Owner-authorized entry')
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
    try:
        patterns = _build_reference_patterns(repository_config())
    except LLMToolError:
        key = worker_key if type(worker_key) is str else 'unknown'
        return PolicyDecision(key, False, 'WORKER_REPOSITORY_CONFIG_INVALID', None)
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
        policy = _parse_policy(raw, patterns)
    except FileNotFoundError:
        return PolicyDecision(worker_key, False, 'WORKER_POLICY_MISSING', None)
    except RuntimeError:
        return PolicyDecision(worker_key, False, 'WORKER_POLICY_CHANGED', None)
    except (OSError, UnicodeError, ValueError, TypeError):
        return PolicyDecision(worker_key, False, 'WORKER_POLICY_INVALID', digest)

    if worker_key not in WORKERS | DEBUG_WORKERS:
        return PolicyDecision(worker_key, False, 'WORKER_UNKNOWN', digest)
    if worker_key not in policy['workers']:
        return PolicyDecision(worker_key, False, 'WORKER_POLICY_ENTRY_MISSING', digest)
    state = policy['workers'][worker_key]['dispatch']
    if state == 'ALLOWED':
        return PolicyDecision(worker_key, True, 'OWNER_POLICY_ALLOWED', digest)
    if state == 'OWNER_ASSIGNMENT_ONLY':
        assignments = policy['workers'][worker_key].get('owner_assignments', [])
        if (type(task_id) is str and _TASK_ID.fullmatch(task_id) is not None and
                type(owner_assignment_ref) is str and
                patterns[0].fullmatch(owner_assignment_ref) is not None and
                {'task_id': task_id, 'owner_assignment_ref': owner_assignment_ref} in assignments):
            return PolicyDecision(worker_key, True, 'OWNER_ASSIGNMENT_MATCHED', digest)
        return PolicyDecision(worker_key, False, 'OWNER_ASSIGNMENT_REQUIRED', digest)
    return PolicyDecision(worker_key, False, 'OWNER_PAUSED', digest)


def audit_entry(decision, action, *, binding=None, authorization_id=None):
    if type(action) is not str or action not in AUDIT_ACTIONS:
        raise WorkerDispatchDenied('WORKER_DISPATCH_ACTION_INVALID')
    entry = {
        'attempt_id': uuid.uuid4().hex,
        'worker_key': decision.worker_key,
        'action': action,
        'decision': 'ALLOWED' if decision.allowed else 'BLOCKED',
        'reason_code': decision.reason_code,
        'policy_sha256': decision.policy_sha256,
        'at': datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
    }
    if binding is not None:
        if (type(binding) is not dict or set(binding) != set(_AUDIT_BINDING_FIELDS) or
                type(binding['assignment_id']) is not str or
                re.fullmatch(r'[0-9a-f]{32}', binding['assignment_id']) is None or
                type(binding['source_sha']) is not str or
                re.fullmatch(r'[0-9a-f]{40}', binding['source_sha']) is None or
                type(binding['contract_digest']) is not str or
                re.fullmatch(r'[0-9a-f]{64}', binding['contract_digest']) is None):
            raise WorkerDispatchDenied('WORKER_DISPATCH_BINDING_INVALID')
        entry.update(binding)
    if authorization_id is not None:
        if (type(authorization_id) is not str or
                re.fullmatch(r'[0-9a-f]{32}', authorization_id) is None or
                binding is None):
            raise WorkerDispatchDenied('WORKER_DISPATCH_BINDING_INVALID')
        entry['authorization_id'] = authorization_id
    return entry


_AUDIT_BINDING_FIELDS = ('assignment_id', 'source_sha', 'contract_digest')


def _assignment_audit_binding(record, developer):
    """Return the validated Task/Run and D6 source binding for one assignment."""
    task_binding = record.get('task_binding') if type(record) is dict else None
    if type(developer) is not dict or type(task_binding) is not dict:
        return None
    assignment_id = developer.get('assignment_id')
    source_sha = developer.get('source_sha')
    contract_digest = task_binding.get('contract_digest')
    if (type(assignment_id) is not str or re.fullmatch(r'[0-9a-f]{32}', assignment_id) is None or
            type(source_sha) is not str or re.fullmatch(r'[0-9a-f]{40}', source_sha) is None or
            type(contract_digest) is not str or
            re.fullmatch(r'[0-9a-f]{64}', contract_digest) is None):
        return None
    return {'assignment_id': assignment_id, 'source_sha': source_sha,
            'contract_digest': contract_digest}


def _matches_assignment_binding(entry, binding):
    return (type(binding) is dict and
            all(entry.get(field) == binding[field] for field in _AUDIT_BINDING_FIELDS))


def _latest_transition_authorization(record, worker_key, action, binding):
    audit = record.get('dispatch_audit', []) if type(record) is dict else []
    for entry in reversed(audit):
        if (type(entry) is dict and entry.get('worker_key') == worker_key and
                entry.get('action') == action and entry.get('decision') == 'ALLOWED' and
                'authorization_id' not in entry and _matches_assignment_binding(entry, binding)):
            return entry.get('attempt_id')
    return None


def _adapter_authorization_block_reason(record, entry):
    """Validate an adapter authorization against its current assignment and transition."""
    if 'authorization_id' not in entry:
        return None
    developer = record.get('developer') if type(record) is dict else None
    binding = _assignment_audit_binding(record, developer)
    if binding is None or not _matches_assignment_binding(entry, binding):
        return 'WORKER_DISPATCH_BINDING_MISMATCH'
    if entry.get('decision') != 'ALLOWED':
        return None
    state_code = _adapter_state_code(record, entry['worker_key'])
    if state_code is not None:
        return state_code
    transition_id = _latest_transition_authorization(
        record, entry['worker_key'], entry['action'], binding)
    if transition_id != entry.get('authorization_id'):
        return 'WORKER_DISPATCH_TRANSITION_NOT_FOUND'
    audit = record.get('dispatch_audit', [])
    if any(type(previous) is dict and previous.get('decision') == 'ALLOWED' and
           previous.get('authorization_id') == transition_id and
           previous.get('worker_key') == entry['worker_key'] and
           previous.get('action') == entry['action'] and
           _matches_assignment_binding(previous, binding)
           for previous in audit):
        return 'WORKER_DISPATCH_AUTHORIZATION_CONSUMED'
    return None


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
    binding = _assignment_audit_binding(current, developer)
    authorization_id = (_latest_transition_authorization(
        current, worker_key, action, binding) if binding is not None else None)
    decision = evaluate(worker_key, owner_assignment_ref, task_id)
    state_code = _adapter_state_code(current, worker_key)
    if state_code is None and binding is None:
        state_code = 'WORKER_DISPATCH_BINDING_INVALID'
    elif state_code is None and authorization_id is None:
        state_code = 'WORKER_DISPATCH_TRANSITION_NOT_FOUND'
    elif state_code is None and any(type(entry) is dict and entry.get('decision') == 'ALLOWED' and
             entry.get('authorization_id') == authorization_id and
             entry.get('worker_key') == worker_key and entry.get('action') == action and
             _matches_assignment_binding(entry, binding)
             for entry in current.get('dispatch_audit', [])):
        state_code = 'WORKER_DISPATCH_AUTHORIZATION_CONSUMED'
    if state_code is not None:
        decision = PolicyDecision(worker_key, False, state_code, decision.policy_sha256)
    entry = audit_entry(decision, action, binding=binding,
                        authorization_id=authorization_id)
    try:
        append = getattr(store, 'append_dispatch_audit')
        saved = append(task_id, entry, expected={
            'developer': current.get('developer'),
            'dispatch_audit': current.get('dispatch_audit'),
        })
    except WorkerDispatchDenied:
        raise
    except Exception:
        raise WorkerDispatchDenied('WORKER_DISPATCH_AUDIT_UNAVAILABLE') from None
    saved_entry = (saved.get('dispatch_audit', [])[-1]
                   if type(saved) is dict and saved.get('dispatch_audit') else None)
    if (type(saved_entry) is not dict or saved_entry.get('decision') != 'ALLOWED'):
        reason = (saved_entry.get('reason_code') if type(saved_entry) is dict else None)
        raise WorkerDispatchDenied(reason or decision.reason_code)
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


_DEVELOPER_IDENTITY_FIELDS = (
    'assignment_id', 'selection_source', 'provider_profile', 'provider_type',
    'model', 'backend_session_semantics', 'session_id', 'source_sha',
    'source_sha_binding', 'owner_assignment_ref',
)
_PROVIDER_TYPES = {
    'company-gw': 'openai-compatible',
    'codex': 'codex-cli',
    'opencode': 'opencode-cli',
    'claude': 'claude-cli',
    'agy': 'agy-cli',
    'gw-agent': 'gw-agent-cli',
}


def _initial_provider_binding(old_developer, new_developer):
    """Allow the first route identity to be recorded on a pending dispatch only."""
    return (
        old_developer.get('state') in ('DEVELOPER_PENDING', 'DEVELOPER_UNAVAILABLE') and
        new_developer.get('state') == 'DEVELOPER_DISPATCHING' and
        old_developer.get('assignment_id') == new_developer.get('assignment_id') and
        old_developer.get('provider_profile') == new_developer.get('provider_profile') and
        old_developer.get('provider_type') is None and old_developer.get('model') is None and
        new_developer.get('provider_type') == _PROVIDER_TYPES.get(
            new_developer.get('provider_profile')) and
        all(old_developer.get(field) == new_developer.get(field)
            for field in _DEVELOPER_IDENTITY_FIELDS
            if field not in ('provider_type', 'model'))
    )


def _gw_agent_served_model_binding(old_developer, new_developer):
    """Allow only a completed gw-agent result to replace its routing alias."""
    if (type(old_developer) is not dict or type(new_developer) is not dict or
            old_developer.get('provider_profile') != 'gw-agent' or
            new_developer.get('provider_profile') != 'gw-agent' or
            old_developer.get('state') != 'DEVELOPER_DISPATCHING' or
            new_developer.get('state') != 'DEVELOPER_RESPONDED' or
            old_developer.get('model') != 'gw'):
        return False
    served_model = new_developer.get('model')
    if type(served_model) is not str or not served_model or served_model == 'gw':
        return False
    if not all(old_developer.get(field) == new_developer.get(field)
               for field in _DEVELOPER_IDENTITY_FIELDS if field != 'model'):
        return False
    execution = new_developer.get('execution')
    if (new_developer.get('error_code') is not None or type(execution) is not dict or
            execution.get('status') != 'DONE' or
            type(execution.get('process_exit_code')) is not int or
            execution.get('process_exit_code') != 0 or
            execution.get('cleanup_status') != 'CONFIRMED'):
        return False
    return all(type(new_developer.get(field)) is str and
               re.fullmatch(r'[0-9a-f]{64}', new_developer[field]) is not None
               for field in ('audit_sha256', 'audit_copy_path_sha256'))


def _developer_identity_changed(old_developer, new_developer):
    changed = any(old_developer.get(field) != new_developer.get(field)
                  for field in _DEVELOPER_IDENTITY_FIELDS)
    return (changed and
            not _initial_provider_binding(old_developer, new_developer) and
            not _gw_agent_served_model_binding(old_developer, new_developer))


def _committed_carry_valid(current, changes, expected, old, new):
    """#1148: a finished Claude run with no new edit carries the D6's committed head.

    Only source_sha moves, from the launch head to the D6 base. The Claude route
    checks the Git facts (registered head, ancestry, committed paths) first."""
    head, base = old.get('source_sha'), new.get('source_sha')
    execution, artifact = new.get('execution'), new.get('artifact')
    callback = new.get('direct_result_callback')
    completion = changes.get('completion', current.get('completion'))
    if not (type(expected) is dict and expected.get('developer') == old and
            all(type(sha) is str and re.fullmatch(r'[0-9a-f]{40}', sha) for sha in (head, base)) and
            all(old.get(field) == new.get(field) for field in (
                'executor_task_id', 'read_paths', 'write_paths', 'acceptance',
                'context_sha256', 'egress_basis', 'input_bytes', 'created_at')) and
            old.get('execution') is None and type(execution) is dict and
            type(callback) is dict and type(completion) is dict and
            type(artifact) is str and artifact and new.get('error_code') is None and
            new.get('artifact_sha256') == hashlib.sha256(artifact.encode('utf-8')).hexdigest()):
        return False
    session, digest = execution.get('native_session_id'), execution.get('stdout_sha256')
    bound = {'task_id': current.get('task_id'), 'run_id': current.get('run_id'),
             'source_sha': base, 'result_head': head}
    return (execution.get('status') == 'DONE' and execution.get('cleanup_status') == 'CONFIRMED' and
            type(execution.get('process_exit_code')) is int and
            execution['process_exit_code'] == 0 and type(session) is str and bool(session) and
            type(digest) is str and re.fullmatch(r'[0-9a-f]{64}', digest) is not None and
            callback.get('executor_task_id') == completion.get('executor_task_id') ==
            new.get('executor_task_id') and
            all(type(item) is dict and all(item.get(key) == value for key, value in bound.items())
                for item in (callback.get('evidence'), completion.get('evidence'))) and
            callback['evidence'].get('carried_result') == {
                'mode': 'NO_RUN_CHANGES', 'assignment_id': old.get('assignment_id'),
                'd6_base': base, 'carried_head': head})


def task_store_attempts(current, changes):
    """Return every gated worker/action touched by one TaskStore transition."""
    old, new = current.get('developer'), changes.get('developer')
    if (type(old) is dict and type(new) is dict and old.get('provider_profile') == 'claude' and
            old.get('state') == 'DEVELOPER_DISPATCHING' and
            new.get('state') == 'DEVELOPER_RESPONDED' and
            old.get('source_sha') != new.get('source_sha') and
            all(old.get(field) == new.get(field)
                for field in _DEVELOPER_IDENTITY_FIELDS if field != 'source_sha')):
        return [('claude-developer', 'COMMITTED_RESULT_CARRY')]
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
        # Gw Agent's pending record is a durable one-shot claim, not provider
        # execution. Let it be recorded while the Owner switch is PAUSED; the
        # transition to DISPATCHING and the adapter launch remain gated.
        if (new_developer.get('provider_profile') == 'gw-agent' and
                new_developer.get('state') == 'DEVELOPER_PENDING'):
            return []
        worker = worker_key_for(new_developer.get('provider_profile'))
        attempts.append((worker or 'unknown', 'DISPATCH'))
        return attempts

    # A timed-out Codex run may continue only after its exact previous record
    # is compared and the committed D6 result head is registered. The policy
    # gate below performs those checks before this action is durably audited.
    if (old_developer.get('provider_profile') == 'codex' and
            old_developer.get('state') == 'DEVELOPER_OUTCOME_UNKNOWN' and
            old_developer.get('error_code') == 'CODEX_TIMEOUT_WITH_CHANGES' and
            new_developer.get('state') == 'DEVELOPER_DISPATCHING' and
            new_developer.get('assignment_id') != old_developer.get('assignment_id')):
        attempts.append(('codex-developer', 'TIMEOUT_CONTINUATION'))
        return attempts

    if _developer_identity_changed(old_developer, new_developer):
        worker = worker_key_for(new_developer.get('provider_profile'))
        attempts.append((worker or 'unknown', 'REASSIGN'))
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
    if provider_profile == 'gw-agent':
        return 'company-gw-agent'
    if provider_profile == 'opencode':
        return 'opencode-developer'
    if provider_profile == 'claude-reviewer':
        return 'claude-reviewer'
    if provider_profile == 'codex':
        return 'codex-developer'
    if provider_profile == 'agy':
        return 'agy'
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
    binding = _assignment_audit_binding(current, new_developer)
    audit = list(current.get('dispatch_audit', []))
    if len(audit) + len(attempts) > MAX_AUDIT_ENTRIES:
        raise WorkerDispatchDenied('WORKER_DISPATCH_AUDIT_LIMIT')
    entries = []
    decisions = []
    for worker_key, action in attempts:
        policy_task_id = current.get('task_id')
        # A bound Claude D6 assignment is authorized against the registered D6
        # identifier named by the Owner policy row. The llmtool Task has its own
        # random identifier and is deliberately not the dispatch authority key.
        if (worker_key == 'claude-developer' and
                type(new_developer) is dict and
                new_developer.get('provider_profile') == 'claude' and
                type(new_developer.get('executor_task_id')) is str):
            policy_task_id = new_developer['executor_task_id']
        decision = evaluate(worker_key, owner_assignment_ref, policy_task_id)
        if action == 'TIMEOUT_CONTINUATION':
            old_execution = (old_developer.get('execution')
                             if type(old_developer) is dict else None)
            new_completion = changes.get('completion', current.get('completion'))
            evidence = (new_completion.get('evidence')
                        if type(new_completion) is dict else None)
            result_head = (evidence.get('result_head')
                           if type(evidence) is dict else None)
            bound = (type(expected) is dict and 'developer' in expected and
                     expected['developer'] == old_developer)
            same_scope = (type(old_developer) is dict and
                          all(old_developer.get(field) == new_developer.get(field)
                              for field in ('provider_profile', 'executor_task_id',
                                            'source_sha', 'read_paths', 'write_paths',
                                            'acceptance')))
            valid_timeout = (
                type(old_execution) is dict and old_execution.get('status') == 'TIMEOUT' and
                old_execution.get('diff_base_sha') == old_developer.get('source_sha') and
                type(old_execution.get('diff_sha256')) is str and
                re.fullmatch(r'[0-9a-f]{64}', old_execution['diff_sha256']) is not None)
            valid_result = (type(result_head) is str and
                            re.fullmatch(r'[0-9a-f]{40}', result_head) is not None and
                            result_head != old_developer.get('source_sha') and
                            type(new_completion) is dict and
                            new_completion.get('executor_task_id') ==
                            new_developer.get('executor_task_id'))
            if not (bound and same_scope and valid_timeout and valid_result):
                decision = PolicyDecision(worker_key, False,
                                          'WORKER_TIMEOUT_CONTINUATION_PRECONDITION',
                                          decision.policy_sha256)
            prior_attempts = sum(
                1 for item in audit
                if type(item) is dict and item.get('worker_key') == worker_key and
                item.get('action') == action)
            if prior_attempts >= MAX_TIMEOUT_CONTINUATIONS_PER_TASK:
                decision = PolicyDecision(worker_key, False,
                                          'WORKER_TIMEOUT_CONTINUATION_LIMIT',
                                          decision.policy_sha256)
        if action == 'COMMITTED_RESULT_CARRY':
            prior = sum(1 for item in audit if type(item) is dict and
                        item.get('action') == action and
                        item.get('assignment_id') in (None, old_developer.get('assignment_id')))
            reason = ('WORKER_COMMITTED_CARRY_LIMIT' if prior >= MAX_COMMITTED_CARRIES else
                      None if _committed_carry_valid(current, changes, expected, old_developer,
                                                     new_developer)
                      else 'WORKER_COMMITTED_CARRY_PRECONDITION')
            if reason is not None:
                decision = PolicyDecision(worker_key, False, reason, decision.policy_sha256)
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
            limit = {'REPAIR': MAX_REPAIR_PER_ASSIGNMENT,
                     'RECOVERY': MAX_RECOVERY_PER_ASSIGNMENT}.get(action)
            if limit is not None:
                assignment_id = old_developer.get('assignment_id')
                prior_attempts = sum(
                    1 for item in audit
                    if type(item) is dict and item.get('worker_key') == worker_key and
                    item.get('action') == action and
                    item.get('assignment_id') in (None, assignment_id))
                if prior_attempts >= limit:
                    reason = ('WORKER_REPAIR_LIMIT_REACHED' if action == 'REPAIR' else
                              'WORKER_RECOVERY_LIMIT_REACHED')
                    decision = PolicyDecision(worker_key, False, reason,
                                              decision.policy_sha256)
        if action == 'REASSIGN':
            bound = (type(expected) is dict and 'developer' in expected and
                     expected['developer'] == old_developer)
            if old_developer.get('state') != 'DEVELOPER_PENDING' or not bound:
                decision = PolicyDecision(worker_key, False,
                                          'WORKER_REASSIGN_PRECONDITION',
                                          decision.policy_sha256)
            elif old_developer.get('assignment_id') == new_developer.get('assignment_id'):
                decision = PolicyDecision(worker_key, False,
                                          'WORKER_REASSIGN_NEW_ASSIGNMENT_REQUIRED',
                                          decision.policy_sha256)
        entries.append(audit_entry(decision, action, binding=binding))
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
