from __future__ import annotations

from dataclasses import dataclass
import sys as _sys
import types as _types

_task_completion = _sys.modules[__package__ + '.task_completion']


def review_dispatch_intent(*, task_id, run_id, contract_digest, developer_task_id,
                           developer_identity, developer_session_ref, source_sha,
                           result_head, diff_sha256, changed_paths, repair_round=0):
    """Build a bounded Reviewer request identity bound to one verified result.

    This is dispatch metadata only. It conveys no provider, Session, review,
    repair, Git, or integration authority.
    """
    if (type(task_id) is not str or re.fullmatch(r'[0-9a-f]{32}', task_id) is None or
            type(run_id) is not str or re.fullmatch(r'[0-9a-f]{32}', run_id) is None or
            type(contract_digest) is not str or _HEX64.fullmatch(contract_digest) is None or
            type(developer_task_id) is not str or _REVIEW_ID.fullmatch(developer_task_id) is None or
            type(developer_identity) is not str or not developer_identity or
            len(developer_identity) > 256 or type(developer_session_ref) is not str or
            not developer_session_ref or len(developer_session_ref) > 512 or
            type(source_sha) is not str or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', source_sha) or
            (result_head is not None and
             (type(result_head) is not str or _HEX40.fullmatch(result_head) is None)) or
            type(diff_sha256) is not str or _HEX64.fullmatch(diff_sha256) is None or
            type(changed_paths) is not list or not changed_paths or
            any(type(path) is not str for path in changed_paths) or
            changed_paths != sorted(set(changed_paths)) or
            any(not path or len(path) > 512 or
                path.startswith('/') or '\\' in path or
                any(part in ('', '.', '..') for part in path.split('/'))
                for path in changed_paths) or
            type(repair_round) is not int or repair_round not in (0, 1)):
        _stop('REVIEW_DISPATCH_BINDING_INVALID')
    value = {
        'schema_version': 1,
        'dispatch_id': secrets.token_hex(16),
        'task_id': task_id,
        'run_id': run_id,
        'contract_digest': contract_digest,
        'developer_task_id': developer_task_id,
        'developer_identity': developer_identity,
        'developer_session_ref': developer_session_ref,
        'source_sha': source_sha,
        'result_head': result_head,
        'diff_sha256': diff_sha256,
        'changed_paths': list(changed_paths),
        'repair_round': repair_round,
        'state': 'REQUESTED',
    }
    value['intent_sha256'] = hashlib.sha256(_canonical_json_bytes(
        value, 'REVIEW_DISPATCH_BINDING_INVALID')).hexdigest()
    return value


def validate_review_dispatch_intent(intent):
    fields = {
        'schema_version', 'dispatch_id', 'task_id', 'run_id', 'contract_digest',
        'developer_task_id', 'developer_identity', 'developer_session_ref',
        'source_sha', 'result_head', 'diff_sha256', 'changed_paths',
        'repair_round', 'state', 'intent_sha256',
    }
    if type(intent) is not dict or set(intent) != fields:
        _stop('REVIEW_DISPATCH_BINDING_INVALID')
    value = dict(intent)
    claimed = value.pop('intent_sha256')
    actual = hashlib.sha256(_canonical_json_bytes(
        value, 'REVIEW_DISPATCH_BINDING_INVALID')).hexdigest()
    if (claimed != actual or type(intent['schema_version']) is not int or
            intent['schema_version'] != 1 or intent['state'] != 'REQUESTED' or
            type(intent['dispatch_id']) is not str or
            re.fullmatch(r'[0-9a-f]{32}', intent['dispatch_id']) is None):
        _stop('REVIEW_DISPATCH_DIGEST_MISMATCH')
    # Reuse the constructor's strict field validation without changing the ID.
    review_dispatch_intent(
        task_id=intent['task_id'], run_id=intent['run_id'],
        contract_digest=intent['contract_digest'],
        developer_task_id=intent['developer_task_id'],
        developer_identity=intent['developer_identity'],
        developer_session_ref=intent['developer_session_ref'],
        source_sha=intent['source_sha'], result_head=intent['result_head'],
        diff_sha256=intent['diff_sha256'], changed_paths=intent['changed_paths'],
        repair_round=intent['repair_round'])
    return intent


def review_dispatch_blocked(intent, code='REVIEW_ADAPTER_UNAVAILABLE'):
    """Return an honest no-launch status receipt for a missing trusted adapter."""
    validate_review_dispatch_intent(intent)
    if type(code) is not str or re.fullmatch(r'[A-Z0-9_]{1,64}', code) is None:
        _stop('REVIEW_DISPATCH_BINDING_INVALID')
    receipt = {
        'schema_version': 2,
        'dispatch_id': intent.get('dispatch_id'),
        'task_id': intent.get('task_id'),
        'run_id': intent.get('run_id'),
        'contract_digest': intent.get('contract_digest'),
        'source_sha': intent.get('source_sha'),
        'result_head': intent.get('result_head'),
        'diff_sha256': intent.get('diff_sha256'),
        'developer_task_id': intent.get('developer_task_id'),
        'reviewer_task_id': None,
        'reviewer_identity': None,
        'reviewer_session_ref': None,
        'review_packet_digest': None,
        'verdict': 'BLOCKED',
        'security': 'BLOCKED',
        'functional': 'BLOCKED',
        'execution_status': 'NOT_DISPATCHED',
        'code': code,
        'findings_sha256': None,
        'provenance': 'LOCAL_NO_ADAPTER',
    }
    receipt['receipt_sha256'] = hashlib.sha256(_canonical_json_bytes(
        receipt, 'REVIEW_RECEIPT_INVALID')).hexdigest()
    return receipt


def review_handoff_record(intent, receipt):
    """Build a received Reviewer outcome and deterministic PM next step."""
    validate_review_dispatch_receipt(intent, receipt)
    if receipt['verdict'] == 'PASS':
        next_step = 'INTEGRATION_GATES_PENDING'
    elif receipt['verdict'] == 'FAIL' and intent['repair_round'] == 0:
        next_step = 'RETURN_TO_DEVELOPER_ONCE'
    elif receipt['verdict'] == 'FAIL':
        next_step = 'STOP_REPAIR_LIMIT'
    else:
        next_step = 'WAIT_FOR_REVIEWER'
    return {'intent': intent, 'dispatch_state': 'RECEIVED', 'receipt': receipt,
            'next_step': next_step, 'error_code': None}


def review_return_record(handoff, *, state='PENDING', response=None,
                         recommendation=None, usage=None, error_code=None):
    """Build the durable, bounded return of a source-bound receipt to its PM."""
    if type(handoff) is not dict or set(handoff) != {
            'intent', 'dispatch_state', 'receipt', 'next_step', 'error_code'}:
        _stop('REVIEW_RECEIPT_INVALID')
    if handoff['dispatch_state'] != 'RECEIVED':
        _stop('REVIEW_RECEIPT_INVALID')
    receipt = handoff.get('receipt')
    if type(receipt) is not dict:
        _stop('REVIEW_RECEIPT_INVALID')
    expected_handoff = review_handoff_record(handoff['intent'], receipt)
    if (_canonical_json_bytes(expected_handoff, 'REVIEW_RECEIPT_INVALID') !=
            _canonical_json_bytes(handoff, 'REVIEW_RECEIPT_INVALID')):
        _stop('REVIEW_RECEIPT_INVALID')
    states = {
        'PENDING': ('NOT_STARTED', None),
        'DISPATCHING': ('CLAIMED', None),
        'RETURNED': ('RETURNED', None),
        'REJECTED': ('RETURNED', 'PM_RESPONSE_REJECTED'),
        'UNAVAILABLE': ('UNAVAILABLE', error_code),
        'OUTCOME_UNKNOWN': ('UNKNOWN', error_code),
    }
    if type(state) is not str or state not in states:
        _stop('REVIEW_RECEIPT_INVALID')
    dispatch, expected_error = states[state]
    if state in ('UNAVAILABLE', 'OUTCOME_UNKNOWN') and (
            type(error_code) is not str or
            re.fullmatch(r'[A-Z0-9_]{1,64}', error_code) is None):
        _stop('REVIEW_RECEIPT_INVALID')
    if state in ('PENDING', 'DISPATCHING', 'RETURNED') and error_code is not None:
        _stop('REVIEW_RECEIPT_INVALID')
    if state == 'REJECTED' and error_code != 'PM_RESPONSE_REJECTED':
        _stop('REVIEW_RECEIPT_INVALID')
    if state == 'RETURNED' and (type(response) is not str or
            type(recommendation) is not dict):
        _stop('REVIEW_RECEIPT_INVALID')
    if state != 'RETURNED' and (response is not None or recommendation is not None or
                                usage is not None):
        _stop('REVIEW_RECEIPT_INVALID')
    next_step = handoff['next_step']
    if state == 'RETURNED':
        decision = recommendation.get('decision')
        intent = handoff['intent']
        verdict = receipt['verdict']
        repair_round = intent['repair_round']
        if not review_return_decision_allowed(verdict, repair_round, decision):
            _stop('REVIEW_RECEIPT_INVALID')
        if decision == 'CONTINUE_INTEGRATION_GATES':
            next_step = 'INTEGRATION_GATES_PENDING'
        elif decision == 'RETURN_TO_DEVELOPER':
            next_step = 'RETURN_TO_DEVELOPER_ONCE'
        elif decision == 'WAIT_FOR_REVIEWER':
            next_step = 'WAIT_FOR_REVIEWER'
        else:
            next_step = 'STOP'
    record = {
        'state': state, 'receipt_sha256': receipt['receipt_sha256'],
        'next_step': next_step, 'pm_dispatch': dispatch,
        'pm_response': response, 'pm_recommendation': recommendation,
        'pm_usage': usage, 'pm_error_code': expected_error,
    }
    _canonical_json_bytes(record, 'REVIEW_RECEIPT_INVALID')
    return record


def validate_review_return_record(record, handoff):
    if type(record) is not dict or set(record) != {
            'state', 'receipt_sha256', 'next_step', 'pm_dispatch', 'pm_response',
            'pm_recommendation', 'pm_usage', 'pm_error_code'}:
        _stop('REVIEW_RECEIPT_INVALID')
    state = record['state']
    expected = review_return_record(handoff, state=state,
        response=record['pm_response'], recommendation=record['pm_recommendation'],
        usage=record['pm_usage'], error_code=record['pm_error_code'])
    if (_canonical_json_bytes(expected, 'REVIEW_RECEIPT_INVALID') !=
            _canonical_json_bytes(record, 'REVIEW_RECEIPT_INVALID')):
        _stop('REVIEW_RECEIPT_INVALID')
    return record


def review_handoff_pending(intent):
    validate_review_dispatch_intent(intent)
    return {'intent': intent, 'dispatch_state': 'PENDING', 'receipt': None,
            'next_step': 'DISPATCH_REVIEWER', 'error_code': None}


def review_handoff_unknown(intent, code='REVIEW_DISPATCH_OUTCOME_UNKNOWN'):
    validate_review_dispatch_intent(intent)
    if type(code) is not str or re.fullmatch(r'[A-Z0-9_]{1,64}', code) is None:
        _stop('REVIEW_DISPATCH_BINDING_INVALID')
    return {'intent': intent, 'dispatch_state': 'OUTCOME_UNKNOWN', 'receipt': None,
            'next_step': 'WAIT_FOR_REVIEWER_STATUS', 'error_code': code}


def review_handoff_dispatching(intent):
    validate_review_dispatch_intent(intent)
    return {'intent': intent, 'dispatch_state': 'DISPATCHING', 'receipt': None,
            'next_step': 'WAIT_FOR_REVIEWER_STATUS', 'error_code': None}


def review_handoff_not_dispatched(intent, receipt):
    validate_review_dispatch_receipt(intent, receipt)
    if receipt['execution_status'] != 'NOT_DISPATCHED':
        _stop('REVIEW_RECEIPT_INVALID')
    return {'intent': intent, 'dispatch_state': 'NOT_DISPATCHED', 'receipt': receipt,
            'next_step': 'WAIT_FOR_REVIEWER', 'error_code': None}


def validate_review_handoff_record(record, *, task_id, run_id, contract_digest,
                                   developer_task_id, developer_identity,
                                   developer_session_ref, source_sha, result_head,
                                   diff_sha256, changed_paths):
    legacy_fields = {'intent', 'receipt', 'next_step'}
    current_fields = {'intent', 'dispatch_state', 'receipt', 'next_step', 'error_code'}
    if type(record) is not dict or set(record) not in (legacy_fields, current_fields):
        _stop('REVIEW_RECEIPT_INVALID')
    intent = validate_review_dispatch_intent(record['intent'])
    expected = (task_id, run_id, contract_digest, developer_task_id,
                developer_identity, developer_session_ref, source_sha, result_head,
                diff_sha256, changed_paths)
    actual = tuple(intent[key] for key in (
        'task_id', 'run_id', 'contract_digest', 'developer_task_id',
        'developer_identity', 'developer_session_ref', 'source_sha', 'result_head',
        'diff_sha256', 'changed_paths'))
    if actual != expected:
        _stop('REVIEW_RECEIPT_BINDING_MISMATCH')
    if set(record) == legacy_fields:
        receipt = validate_review_dispatch_receipt(intent, record['receipt'])
        if (review_handoff_record(intent, receipt)['next_step'] != record['next_step'] or
                record['next_step'] not in ('INTEGRATION_GATES_PENDING',
                    'RETURN_TO_DEVELOPER_ONCE', 'STOP_REPAIR_LIMIT', 'WAIT_FOR_REVIEWER')):
            _stop('REVIEW_RECEIPT_INVALID')
        return record
    state = record['dispatch_state']
    receipt = record['receipt']
    if state == 'PENDING':
        expected_record = review_handoff_pending(intent)
    elif state == 'DISPATCHING':
        expected_record = review_handoff_dispatching(intent)
    elif state == 'OUTCOME_UNKNOWN':
        expected_record = review_handoff_unknown(intent, record['error_code'])
    elif state == 'NOT_DISPATCHED':
        expected_record = review_handoff_not_dispatched(intent, receipt)
    elif state == 'RECEIVED':
        expected_record = review_handoff_record(intent, receipt)
    else:
        _stop('REVIEW_RECEIPT_INVALID')
    if (_canonical_json_bytes(expected_record, 'REVIEW_RECEIPT_INVALID') !=
            _canonical_json_bytes(record, 'REVIEW_RECEIPT_INVALID')):
        _stop('REVIEW_RECEIPT_INVALID')
    return record


def normalize_review_handoff_record(record, **binding):
    """Normalize the legacy three-field receipt shape to a terminal phase."""
    validate_review_handoff_record(record, **binding)
    if 'dispatch_state' in record:
        return record
    receipt = record['receipt']
    if receipt['execution_status'] == 'NOT_DISPATCHED':
        return review_handoff_not_dispatched(record['intent'], receipt)
    return review_handoff_record(record['intent'], receipt)


@dataclass(frozen=True)
class D6Registration:
    task_id: str
    trusted_agent_id: str
    session_ref: str
    root: str
    common_dir: str
    branch: str
    source_sha: str
    result_sha: str
    state: str
    lock_state: str
    device: int
    inode: int
    uid: int
    native_session_id: str | None
    github_login: str | None
    env2_pass_marker: object
    env2_approved_tooling_sha: str | None


def read_d6_registration(task_id, common_dir, *, allowed_states=('ACTIVE',)):
    """Read one bounded owner-only D6 record without following links."""
    if (type(task_id) is not str or _TASK_ID.fullmatch(task_id) is None or
            type(common_dir) is not str or not os.path.isabs(common_dir) or
            os.path.realpath(common_dir) != common_dir or not common_dir.endswith('/.git')):
        _stop('D6_REGISTRY_ID_INVALID')
    path = Path(common_dir) / 'llmtool-worktrees' / (task_id + '.json')
    try:
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or
                stat.S_IMODE(parent.st_mode) != 0o700):
            _stop('D6_REGISTRY_UNAVAILABLE')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
                before.st_size > _REGISTRY_LIMIT):
            os.close(fd)
            _stop('D6_REGISTRY_INVALID')
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(_REGISTRY_LIMIT + 1)
            again = os.fstat(stream.fileno())
        after = path.lstat()
        if (len(raw) > _REGISTRY_LIMIT or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (again.st_dev, again.st_ino, again.st_size, again.st_mtime_ns) or
                (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            _stop('D6_REGISTRY_CHANGED')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _: _stop('D6_REGISTRY_INVALID'))
    except LLMToolError:
        raise
    except Exception:
        _stop('D6_REGISTRY_UNAVAILABLE')
    if (type(value) is not dict or value.get('schema_version') != 1 or
            value.get('task_id') != task_id or value.get('state') not in allowed_states or
            value.get('lock_state') != 'LOCKED' or
            value.get('git_common_dir') != common_dir or
            type(value.get('trusted_agent_id')) is not str or not value['trusted_agent_id'] or
            type(value.get('session_ref')) is not str or not value['session_ref'] or
            type(value.get('root_realpath')) is not str or
            not os.path.isabs(value['root_realpath']) or
            os.path.realpath(value['root_realpath']) != value['root_realpath'] or
            type(value.get('branch')) is not str or not value['branch'] or
            type(value.get('base_sha')) is not str or _HEX40.fullmatch(value['base_sha']) is None or
            type(value.get('head_sha')) is not str or _HEX40.fullmatch(value['head_sha']) is None):
        _stop('D6_REGISTRY_INVALID')
    identity = value.get('physical_identity')
    identity_keys = set(identity) if type(identity) is dict else None
    legacy_identity = identity_keys == {'device', 'inode', 'uid'}
    lifecycle_identity = identity_keys == {'device', 'inode', 'uid', 'mode'}
    if (not (legacy_identity or lifecycle_identity) or
            (lifecycle_identity and
             (type(identity['mode']) is not str or identity['mode'] != '0o700')) or
            any(type(identity[k]) is not int or identity[k] < 0
                for k in ('device', 'inode', 'uid'))):
        _stop('D6_REGISTRY_INVALID')
    try:
        root_stat = Path(value['root_realpath']).lstat()
    except OSError:
        _stop('D6_ROOT_MISSING')
    if ((root_stat.st_dev, root_stat.st_ino, root_stat.st_uid) !=
            (identity['device'], identity['inode'], identity['uid']) or
            identity['uid'] != os.getuid() or
            stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode) or
            stat.S_IMODE(root_stat.st_mode) != 0o700):
        _stop('D6_ROOT_IDENTITY_MISMATCH')
    native_session = value.get('native_session_id')
    if native_session is not None and (type(native_session) is not str or
            not native_session or len(native_session) > 256 or
            any(ord(c) < 0x20 or ord(c) == 0x7f for c in native_session)):
        _stop('D6_REGISTRY_INVALID')
    github_login = value.get('github_login')
    if github_login is not None and (type(github_login) is not str or
            not re.fullmatch(r'[A-Za-z0-9-]{1,39}', github_login)):
        _stop('D6_REGISTRY_INVALID')
    approved_tooling_sha = value.get('env2_approved_tooling_sha')
    if approved_tooling_sha is not None and (
            type(approved_tooling_sha) is not str or
            _HEX40.fullmatch(approved_tooling_sha) is None or
            approved_tooling_sha != value['head_sha']):
        _stop('D6_REGISTRY_INVALID')
    return D6Registration(task_id, value['trusted_agent_id'], value['session_ref'],
                          value['root_realpath'], common_dir, value['branch'],
                          value['base_sha'], value['head_sha'], value['state'],
                          value['lock_state'], identity['device'], identity['inode'],
                          identity['uid'], native_session, github_login,
                          value.get('env2_pass_marker'), approved_tooling_sha)


def _registered_test(path):
    if type(path) is not str:
        return None
    for pattern, kind in _TEST_REGISTRY:
        if pattern.fullmatch(path):
            return kind
    return None


def _docs_only(paths):
    return (bool(paths) and all(type(path) is str and path.endswith('.md')
            and not path.startswith(('tools/llmtool/llmtool/', 'scripts/'))
            for path in paths))


def _docs_only_ci(paths):
    """Classify the exact D6 result paths using Linux CI's `classify` step."""
    # Keep this aligned with the classify step in .github/workflows/llmtool-linux-ci.yml.
    if type(paths) is not list or not paths:
        return False
    for path in paths:
        if type(path) is not str or not path or path.startswith('/') or '\\' in path:
            return False
        parts = path.split('/')
        if any(part in ('', '.', '..') for part in parts):
            return False
        if path.startswith('tools/llmtool/'):
            relative_parts = parts[2:]
            if (len(relative_parts) == 1 and path.endswith('.md')):
                continue
            if (len(relative_parts) == 2 and path.endswith('.md')):
                return False
            return False
        if path.startswith('.claude/skills/') and path.endswith('.py'):
            return False
        if (path.endswith('.md') or path in ('AGENTS.md', 'CLAUDE.md') or
                path.startswith(('.claude/skills/', 'docs/'))):
            continue
        return False
    return True


__all__ = [
    'review_dispatch_intent', 'validate_review_dispatch_intent',
    'review_dispatch_blocked', 'review_handoff_record',
    'review_return_record',
    'validate_review_return_record', 'review_handoff_pending',
    'review_handoff_unknown', 'review_handoff_dispatching',
    'review_handoff_not_dispatched', 'validate_review_handoff_record',
    'normalize_review_handoff_record', 'D6Registration', 'read_d6_registration',
    '_registered_test', '_docs_only', '_docs_only_ci',
]

for _task_completion_name in __all__:
    _task_completion_value = globals()[_task_completion_name]
    if isinstance(_task_completion_value, _types.FunctionType):
        _task_completion_rebound = _types.FunctionType(
            _task_completion_value.__code__, _task_completion.__dict__,
            _task_completion_value.__name__, _task_completion_value.__defaults__,
            _task_completion_value.__closure__)
        _task_completion_rebound.__kwdefaults__ = _task_completion_value.__kwdefaults__
        _task_completion_rebound.__dict__.update(_task_completion_value.__dict__)
        _task_completion_rebound.__module__ = _task_completion_value.__module__
        _task_completion_rebound.__qualname__ = _task_completion_value.__qualname__
        _task_completion_rebound.__doc__ = _task_completion_value.__doc__
        globals()[_task_completion_name] = _task_completion_rebound
del _task_completion_name, _task_completion_value
