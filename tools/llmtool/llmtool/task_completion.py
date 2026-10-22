"""Fail-closed evidence collection for an already completed D6 Executor task.

This module never applies a patch or writes Git state. A D6 registry record is a
cooperative local provenance source; it is not a same-UID security boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

from .errors import LLMToolError

_TASK_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,95}\Z')
_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_REGISTRY_LIMIT = 64 * 1024
_GIT_LIMIT = 2 * 1024 * 1024
_PROPOSAL_FILE_LIMIT = 128
_PROPOSAL_TREE_LIMIT = 8 * 1024 * 1024
_TIMEOUT = 10
_TEST_TIMEOUT = 180
_TEST_OUTPUT_LIMIT = 256 * 1024
_REQUIRED_CHECKS = frozenset(('check (3.9)', 'check (3.14)'))
_D6_EVIDENCE_STATES = frozenset(('ACTIVE', 'REVIEW_PENDING', 'REPAIR'))
_CI_WORKFLOW_PATH = '.github/workflows/llmtool-ci.yml'
_CI_WORKFLOW_NAME = 'llmtool CI'
_GITHUB_ACTIONS_APP_ID = 15368
_TEST_PATH = re.compile(r'tools/llmtool/tests/test_[A-Za-z0-9_]+\.py\Z')
_REVIEW_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,95}\Z')
_REVIEW_VERDICTS = frozenset(('PASS', 'FAIL', 'BLOCKED'))


def _stop(code):
    error = LLMToolError('Executor completion evidence is unavailable; details omitted')
    error.completion_code = code
    raise error from None


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _stop('D6_REGISTRY_INVALID')
        result[key] = value
    return result


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
    value['intent_sha256'] = hashlib.sha256(json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
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
    actual = hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    if (claimed != actual or intent['schema_version'] != 1 or intent['state'] != 'REQUESTED' or
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
        'schema_version': 1,
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
        'verdict': 'BLOCKED',
        'security': 'BLOCKED',
        'functional': 'BLOCKED',
        'execution_status': 'NOT_DISPATCHED',
        'code': code,
        'findings_sha256': None,
        'provenance': 'LOCAL_NO_ADAPTER',
    }
    receipt['receipt_sha256'] = hashlib.sha256(json.dumps(
        receipt, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
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


def review_return_decision_allowed(verdict, repair_round, decision):
    if type(repair_round) is not int or repair_round not in (0, 1):
        return False
    if verdict == 'PASS':
        allowed = ('CONTINUE_INTEGRATION_GATES', 'STOP')
    elif verdict == 'FAIL' and repair_round == 0:
        allowed = ('RETURN_TO_DEVELOPER', 'STOP')
    elif verdict == 'FAIL' and repair_round == 1:
        allowed = ('STOP',)
    elif verdict == 'BLOCKED':
        allowed = ('WAIT_FOR_REVIEWER', 'STOP')
    else:
        return False
    return decision in allowed


def review_return_record(handoff, *, state='PENDING', response=None,
                         recommendation=None, usage=None, error_code=None):
    """Build the durable, bounded return of a source-bound receipt to its PM."""
    if type(handoff) is not dict or handoff.get('dispatch_state') != 'RECEIVED':
        _stop('REVIEW_RECEIPT_INVALID')
    receipt = handoff.get('receipt')
    if type(receipt) is not dict:
        _stop('REVIEW_RECEIPT_INVALID')
    states = {
        'PENDING': ('NOT_STARTED', None),
        'DISPATCHING': ('CLAIMED', None),
        'RETURNED': ('RETURNED', None),
        'REJECTED': ('RETURNED', 'PM_RESPONSE_REJECTED'),
        'UNAVAILABLE': ('UNAVAILABLE', error_code),
        'OUTCOME_UNKNOWN': ('UNKNOWN', error_code),
    }
    if state not in states:
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
    return {
        'state': state, 'receipt_sha256': receipt['receipt_sha256'],
        'next_step': next_step, 'pm_dispatch': dispatch,
        'pm_response': response, 'pm_recommendation': recommendation,
        'pm_usage': usage, 'pm_error_code': expected_error,
    }


def validate_review_return_record(record, handoff):
    if type(record) is not dict or set(record) != {
            'state', 'receipt_sha256', 'next_step', 'pm_dispatch', 'pm_response',
            'pm_recommendation', 'pm_usage', 'pm_error_code'}:
        _stop('REVIEW_RECEIPT_INVALID')
    state = record['state']
    expected = review_return_record(handoff, state=state,
        response=record['pm_response'], recommendation=record['pm_recommendation'],
        usage=record['pm_usage'], error_code=record['pm_error_code'])
    if expected != record:
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
    if expected_record != record:
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


def validate_review_dispatch_receipt(intent, receipt):
    """Validate a typed-in-process Reviewer adapter result against the intent.

    The injected adapter is a trusted host boundary. This check binds its DATA
    to the exact Task/Run/contract/source/result and rejects self-review. It is
    not a signature, Session authentication, or provider-execution proof.
    """
    fields = {
        'schema_version', 'dispatch_id', 'task_id', 'run_id', 'contract_digest',
        'source_sha', 'result_head', 'diff_sha256', 'developer_task_id',
        'reviewer_task_id', 'reviewer_identity', 'reviewer_session_ref', 'verdict',
        'security', 'functional', 'execution_status', 'code', 'findings_sha256',
        'provenance', 'receipt_sha256',
    }
    if type(intent) is not dict or type(receipt) is not dict or set(receipt) != fields:
        _stop('REVIEW_RECEIPT_INVALID')
    validate_review_dispatch_intent(intent)
    value = dict(receipt)
    claimed = value.pop('receipt_sha256')
    actual = hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    if claimed != actual:
        _stop('REVIEW_RECEIPT_DIGEST_MISMATCH')
    if (type(receipt['schema_version']) is not int or receipt['schema_version'] != 1 or
            any(receipt.get(key) != intent.get(key) for key in (
                'dispatch_id', 'task_id', 'run_id', 'contract_digest', 'source_sha',
                'result_head', 'diff_sha256', 'developer_task_id')) or
            type(receipt['verdict']) is not str or receipt['verdict'] not in _REVIEW_VERDICTS or
            type(receipt['security']) is not str or receipt['security'] not in _REVIEW_VERDICTS or
            type(receipt['functional']) is not str or receipt['functional'] not in _REVIEW_VERDICTS or
            type(receipt['execution_status']) is not str or
            receipt['execution_status'] not in ('SUCCEEDED', 'FAILED', 'NOT_DISPATCHED') or
            type(receipt['code']) is not str or
            re.fullmatch(r'[A-Z0-9_]{1,64}', receipt['code']) is None or
            type(receipt['provenance']) is not str or
            receipt['provenance'] not in ('TRUSTED_REVIEW_ADAPTER', 'LOCAL_NO_ADAPTER')):
        _stop('REVIEW_RECEIPT_BINDING_MISMATCH')
    if receipt['execution_status'] == 'NOT_DISPATCHED':
        if (receipt['verdict'] != 'BLOCKED' or receipt['security'] != 'BLOCKED' or
                receipt['functional'] != 'BLOCKED' or
                receipt['provenance'] != 'LOCAL_NO_ADAPTER' or
                any(receipt.get(key) is not None for key in (
                    'reviewer_task_id', 'reviewer_identity', 'reviewer_session_ref',
                    'findings_sha256'))):
            _stop('REVIEW_RECEIPT_INVALID')
    else:
        for key in ('reviewer_task_id', 'reviewer_identity', 'reviewer_session_ref'):
            if type(receipt.get(key)) is not str or not receipt[key] or len(receipt[key]) > 512:
                _stop('REVIEW_RECEIPT_INVALID')
        if (receipt['reviewer_task_id'] == intent['developer_task_id'] or
                receipt['reviewer_identity'] == intent['developer_identity'] or
                receipt['reviewer_session_ref'] == intent['developer_session_ref'] or
                type(receipt['findings_sha256']) is not str or
                _HEX64.fullmatch(receipt['findings_sha256']) is None or
                receipt['provenance'] != 'TRUSTED_REVIEW_ADAPTER'):
            _stop('REVIEWER_NOT_INDEPENDENT')
        if receipt['execution_status'] == 'SUCCEEDED':
            expected = ('PASS' if receipt['security'] == receipt['functional'] == 'PASS' else
                        'FAIL' if 'FAIL' in (receipt['security'], receipt['functional']) and
                        'BLOCKED' not in (receipt['security'], receipt['functional']) else 'BLOCKED')
            if receipt['verdict'] != expected:
                _stop('REVIEW_RECEIPT_INVALID')
        elif receipt['verdict'] != 'BLOCKED':
            _stop('REVIEW_RECEIPT_INVALID')
    return receipt


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
    if (type(identity) is not dict or
            set(identity) != {'device', 'inode', 'uid'} or
            any(type(identity[k]) is not int or identity[k] < 0 for k in identity)):
        _stop('D6_REGISTRY_INVALID')
    try:
        root_stat = Path(value['root_realpath']).lstat()
    except OSError:
        _stop('D6_ROOT_MISSING')
    if ((root_stat.st_dev, root_stat.st_ino, root_stat.st_uid) !=
            (identity['device'], identity['inode'], identity['uid']) or
            identity['uid'] != os.getuid() or
            stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode)):
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
    return D6Registration(task_id, value['trusted_agent_id'], value['session_ref'],
                          value['root_realpath'], common_dir, value['branch'],
                          value['base_sha'], value['head_sha'], value['state'],
                          value['lock_state'], identity['device'], identity['inode'],
                          identity['uid'], native_session, github_login)


def _git(root, args, *, limit=_GIT_LIMIT):
    """Run one fixed Git read with bounded output and no inherited Git config."""
    env = {
        'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_ATTR_NOSYSTEM': '1',
        'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_LAZY_FETCH': '1',
        'GIT_TERMINAL_PROMPT': '0', 'GIT_PAGER': 'cat', 'PAGER': 'cat',
        'GIT_LITERAL_PATHSPECS': '1',
    }
    safe = ('-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
            '-c', 'core.untrackedCache=false', '-c', 'core.attributesFile=/dev/null',
            '-c', 'core.pager=cat', '-c', 'color.ui=false', '-c',
            'protocol.allow=never')
    try:
        process = subprocess.Popen(['/usr/bin/git', '--no-pager', *safe, *args],
            cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, shell=False, close_fds=True, start_new_session=True)
    except OSError:
        _stop('GIT_INSPECTION_UNAVAILABLE')
    selector = selectors.DefaultSelector()
    out, errors = bytearray(), 0
    deadline = time.monotonic() + _TIMEOUT
    try:
        for stream, kind in ((process.stdout, 'out'), (process.stderr, 'err')):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, kind)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop('GIT_INSPECTION_TIMEOUT')
            for key, _ in selector.select(min(remaining, .1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif key.data == 'out':
                    if len(out) + len(chunk) > limit:
                        _stop('GIT_EVIDENCE_TOO_LARGE')
                    out.extend(chunk)
                else:
                    errors += len(chunk)
                    if errors > 16 * 1024:
                        _stop('GIT_INSPECTION_FAILED')
        if time.monotonic() >= deadline or process.wait(timeout=max(0, deadline-time.monotonic())) != 0:
            _stop('GIT_INSPECTION_FAILED')
        if errors:
            _stop('GIT_INSPECTION_FAILED')
        return bytes(out)
    except subprocess.TimeoutExpired:
        _stop('GIT_INSPECTION_TIMEOUT')
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, 9)
            except OSError:
                process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()


@dataclass(frozen=True)
class GitSnapshot:
    root: str
    common_dir: str
    branch: str
    head: str
    clean: bool


def collect_git_snapshot(root):
    root = os.path.realpath(root)
    actual_root = _git(root, ('rev-parse', '--show-toplevel'), limit=4096).decode().strip()
    common = _git(root, ('rev-parse', '--path-format=absolute', '--git-common-dir'),
                  limit=4096).decode().strip()
    branch = _git(root, ('branch', '--show-current'), limit=4096).decode().strip()
    head = _git(root, ('rev-parse', '--verify', 'HEAD'), limit=4096).decode().strip()
    status = _git(root, ('status', '--porcelain=v1', '-z', '--untracked-files=all'), limit=1024*1024)
    if (actual_root != root or not branch or _HEX40.fullmatch(head) is None or
            os.path.realpath(common) != common):
        _stop('GIT_BINDING_INVALID')
    return GitSnapshot(root, common, branch, head, status == b'')


def bind_executor_task(task_id, *, expected_common_dir, root=None):
    """Bind a proposal to the current D6 root and its registered branch HEAD."""
    root = os.getcwd() if root is None else root
    registration = read_d6_registration(task_id, expected_common_dir)
    snapshot = collect_git_snapshot(root)
    if (snapshot.root != registration.root or snapshot.common_dir != registration.common_dir or
            snapshot.branch != registration.branch or snapshot.head != registration.result_sha or
            not snapshot.clean):
        _stop('D6_SOURCE_BINDING_MISMATCH')
    try:
        _git(root, ('merge-base', '--is-ancestor', registration.source_sha, snapshot.head),
             limit=4096)
    except LLMToolError:
        _stop('D6_SOURCE_BINDING_MISMATCH')
    return registration, snapshot


def validate_task_binding(*, stored_binding, task_id, run_id, contract_digest):
    """Compare the durable TaskBinding to the identifiers bound at D6 admission."""
    if (type(stored_binding) is not dict or
            stored_binding.get('task_id') != task_id or
            stored_binding.get('run_id') != run_id or
            stored_binding.get('contract_digest') != contract_digest or
            type(task_id) is not str or type(run_id) is not str or
            type(contract_digest) is not str):
        _stop('TASK_RUN_CONTRACT_MISMATCH')
    return True


def validate_host_worker_receipt(packet, *, task_binding, task_id, run_id,
                                 contract_digest, source_sha):
    """Validate the minimal typed receipt/delivery fields linked by the Host seam."""
    required = {
        'task_binding', 'job_digest', 'agent_id', 'source_sha',
        'receipt_verdict', 'receipt_reason', 'receipt_accepted', 'receipt_digest',
        'delivery_state', 'delivery_reason', 'delivery_ack', 'delivery_accepted',
        'delivery_task_id', 'delivery_agent_id', 'delivery_job_digest',
        'delivery_source_sha', 'delivery_integrator_session',
        'delivery_receipt_digest', 'delivery_receipt_verdict',
        'delivery_receipt_reason', 'delivery_digest',
    }
    return (type(packet) is dict and set(packet) == required and
            type(task_binding) is dict and
            packet.get('task_binding') == task_binding and
            packet.get('task_binding') == {'task_id': task_id, 'run_id': run_id,
                'contract_version': task_binding.get('contract_version'),
                'contract_digest': contract_digest} and
            packet.get('source_sha') == source_sha and
            type(source_sha) is str and _HEX40.fullmatch(source_sha) is not None and
            type(packet.get('job_digest')) is str and
            re.fullmatch(r'[0-9a-f]{64}', packet['job_digest']) is not None and
            type(packet.get('agent_id')) is str and bool(packet['agent_id']) and
            packet.get('receipt_verdict') == 'SUCCESS' and
            packet.get('receipt_reason') == 'WORKER_RUN_OBSERVED' and
            packet.get('receipt_accepted') is False and
            type(packet.get('receipt_digest')) is str and
            re.fullmatch(r'[0-9a-f]{64}', packet['receipt_digest']) is not None and
            packet.get('delivery_state') == 'DELIVERED' and
            packet.get('delivery_reason') == 'RECEIPT_DELIVERED' and
            packet.get('delivery_ack') == 'CALLER_RETURNED' and
            packet.get('delivery_accepted') is False and
            packet.get('delivery_task_id') == task_id and
            packet.get('delivery_agent_id') == packet.get('agent_id') and
            packet.get('delivery_job_digest') == packet.get('job_digest') and
            packet.get('delivery_source_sha') == source_sha and
            type(packet.get('delivery_integrator_session')) is str and
            bool(packet['delivery_integrator_session']) and
            packet.get('delivery_receipt_digest') == packet.get('receipt_digest') and
            packet.get('delivery_receipt_verdict') == packet.get('receipt_verdict') and
            packet.get('delivery_receipt_reason') == packet.get('receipt_reason') and
            type(packet.get('delivery_digest')) is str and
            re.fullmatch(r'[0-9a-f]{64}', packet['delivery_digest']) is not None)


def collect_executor_result(*, task_id, expected_common_dir, expected_root,
                           expected_branch, source_sha, task_binding,
                           expected_agent, expected_session_ref,
                           expected_native_session_id, write_paths, artifact, artifact_sha256):
    """Compare the current registered Executor HEAD and actual source-to-result diff."""
    registration = read_d6_registration(task_id, expected_common_dir,
                                        allowed_states=_D6_EVIDENCE_STATES)
    if (registration.root != expected_root or registration.branch != expected_branch or
            type(source_sha) is not str or _HEX40.fullmatch(source_sha) is None or
            registration.trusted_agent_id != expected_agent or
            registration.session_ref != expected_session_ref or
            registration.native_session_id != expected_native_session_id):
        _stop('D6_TASK_BINDING_MISMATCH')
    if (type(task_binding) is not dict or not task_binding or
            any(type(task_binding.get(key)) is not str for key in
                ('task_id', 'run_id', 'contract_digest'))):
        _stop('TASK_BINDING_MISSING')
    snapshot = collect_git_snapshot(registration.root)
    if (snapshot.common_dir != registration.common_dir or snapshot.branch != registration.branch or
            snapshot.head != registration.result_sha or not snapshot.clean):
        _stop('RESULT_HEAD_MISMATCH')
    try:
        _git(registration.root, ('merge-base', '--is-ancestor', registration.source_sha, source_sha),
             limit=4096)
    except LLMToolError:
        _stop('D6_TASK_BINDING_MISMATCH')
    if source_sha == snapshot.head:
        _stop('RESULT_DIFF_EMPTY')
    ancestor = subprocess.run(['/usr/bin/git', '-C', registration.root, 'merge-base',
        '--is-ancestor', source_sha, snapshot.head], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=_TIMEOUT,
        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
             'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0',
             'GIT_NO_LAZY_FETCH': '1'}, shell=False, check=False)
    if ancestor.returncode != 0:
        _stop('SOURCE_NOT_ANCESTOR')
    changed = _git(registration.root, ('diff', '--name-only', '-z', '--no-ext-diff',
                  '--no-textconv', '--no-renames', source_sha, snapshot.head, '--'),
                   limit=1024*1024)
    paths = tuple(sorted(set(path for path in changed.decode('utf-8', 'strict').split('\x00') if path)))
    if not paths or len(paths) > 128:
        _stop('RESULT_DIFF_INVALID')
    if not set(paths).issubset(set(write_paths)):
        _stop('RESULT_SCOPE_MISMATCH')
    patch = _git(registration.root, ('diff', '--binary', '--no-ext-diff', '--no-textconv',
                '--no-renames', source_sha, snapshot.head, '--'), limit=_GIT_LIMIT)
    verify_artifact_applied(registration.root, artifact, artifact_sha256,
                            source_sha, snapshot.head, paths, write_paths)
    return {
        'task_id': task_binding.get('task_id'), 'run_id': task_binding.get('run_id'),
        'executor_task_id': registration.task_id,
        'executor_identity': registration.trusted_agent_id,
        'executor_session_ref': registration.session_ref,
        'executor_native_session_id': registration.native_session_id,
        'executor_native_session': ('VERIFIED' if registration.native_session_id else 'UNKNOWN'),
        'root': registration.root, 'common_dir': registration.common_dir,
        'branch': registration.branch,
        'source_sha': source_sha, 'result_head': snapshot.head,
        'changed_paths': list(paths),
        'diff_sha256': hashlib.sha256(patch).hexdigest(),
    }


def _worktree_snapshot(*, root, source_sha, read_paths, write_paths):
    """Hash a registered uncommitted result without promoting it to a commit."""
    snapshot = collect_git_snapshot(root)
    if snapshot.head != source_sha:
        _stop('WORKTREE_SOURCE_HEAD_MISMATCH')
    untracked = _git(root, ('ls-files', '--others', '--exclude-standard', '-z'),
                     limit=64 * 1024)
    if untracked:
        _stop('WORKTREE_UNTRACKED_FILES')
    status = _git(root, ('status', '--porcelain=v1', '-z', '--untracked-files=all'),
                   limit=1024 * 1024)
    patch = _git(root, ('diff', '--binary', '--no-ext-diff', '--no-textconv',
                        '--no-renames', source_sha, '--'), limit=_GIT_LIMIT)
    changed_raw = _git(root, ('diff', '--name-only', '-z', '--no-ext-diff',
                              '--no-textconv', '--no-renames', source_sha, '--'),
                       limit=1024 * 1024)
    try:
        changed = tuple(sorted(set(path for path in
            changed_raw.decode('utf-8', 'strict').split('\x00') if path)))
    except UnicodeError:
        _stop('WORKTREE_PATH_INVALID')
    if (not changed or len(changed) > _PROPOSAL_FILE_LIMIT or
            not set(changed).issubset(set(write_paths))):
        _stop('WORKTREE_SCOPE_MISMATCH')
    for path in changed:
        parts = Path(path).parts
        if (Path(path).is_absolute() or not parts or
                any(part in ('', '.', '..') for part in parts) or '\\' in path or
                any(ord(char) < 0x20 or ord(char) >= 0x7f for char in path)):
            _stop('WORKTREE_PATH_INVALID')

    paths = sorted(set(read_paths) | set(write_paths))
    if not paths or len(paths) > 32:
        _stop('WORKTREE_SCOPE_INVALID')
    file_records = []
    total = 0
    for relative in paths:
        if (type(relative) is not str or not relative or relative.startswith('/') or
                '\\' in relative or any(part in ('', '.', '..') for part in relative.split('/'))):
            _stop('WORKTREE_PATH_INVALID')
        target = Path(root).joinpath(*relative.split('/'))
        try:
            before = target.lstat()
        except FileNotFoundError:
            file_records.append([relative, None, None, None])
            continue
        except OSError:
            _stop('WORKTREE_FILE_UNAVAILABLE')
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size > _GIT_LIMIT):
            _stop('WORKTREE_FILE_UNSUPPORTED')
        total += before.st_size
        if total > _PROPOSAL_TREE_LIMIT:
            _stop('WORKTREE_FILES_TOO_LARGE')
        try:
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                opened = os.fstat(fd)
                if (not stat.S_ISREG(opened.st_mode) or
                        (opened.st_dev, opened.st_ino, opened.st_size) !=
                        (before.st_dev, before.st_ino, before.st_size)):
                    _stop('WORKTREE_FILE_CHANGED')
                digest = hashlib.sha256()
                size = 0
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > _GIT_LIMIT:
                        _stop('WORKTREE_FILE_TOO_LARGE')
                    digest.update(chunk)
                after = os.fstat(fd)
                current = target.lstat()
                if ((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) !=
                        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or
                        (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino) or
                        size != opened.st_size):
                    _stop('WORKTREE_FILE_CHANGED')
            finally:
                os.close(fd)
        except LLMToolError:
            raise
        except Exception:
            _stop('WORKTREE_FILE_UNAVAILABLE')
        file_records.append([relative, before.st_mode & 0o777, size, digest.hexdigest()])
    manifest = json.dumps(file_records, ensure_ascii=True, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')
    return {
        'root': snapshot.root, 'common_dir': snapshot.common_dir,
        'branch': snapshot.branch, 'source_sha': snapshot.head,
        'changed_paths': list(changed), 'diff': patch,
        'diff_sha256': hashlib.sha256(patch).hexdigest(),
        'status_sha256': hashlib.sha256(status).hexdigest(),
        'file_manifest_sha256': hashlib.sha256(manifest).hexdigest(),
    }


def collect_executor_worktree_result(*, task_id, expected_common_dir, expected_root,
                                     expected_branch, source_sha, task_binding,
                                     expected_agent, expected_session_ref,
                                     expected_native_session_id, write_paths, read_paths,
                                     artifact, artifact_sha256):
    """Bind a real D6 uncommitted diff and refuse to call it a result commit."""
    registration = read_d6_registration(task_id, expected_common_dir,
                                        allowed_states=_D6_EVIDENCE_STATES)
    if (registration.root != expected_root or registration.branch != expected_branch or
            registration.result_sha != source_sha or registration.trusted_agent_id != expected_agent or
            registration.session_ref != expected_session_ref or
            registration.native_session_id != expected_native_session_id or
            type(source_sha) is not str or _HEX40.fullmatch(source_sha) is None):
        _stop('D6_TASK_BINDING_MISMATCH')
    if (type(task_binding) is not dict or
            any(type(task_binding.get(key)) is not str for key in
                ('task_id', 'run_id', 'contract_digest'))):
        _stop('TASK_BINDING_MISSING')
    live = _worktree_snapshot(root=registration.root, source_sha=source_sha,
                              read_paths=read_paths, write_paths=write_paths)
    if (live['common_dir'] != registration.common_dir or
            live['branch'] != registration.branch):
        _stop('D6_TASK_BINDING_MISMATCH')
    try:
        _git(registration.root, ('merge-base', '--is-ancestor', registration.source_sha,
                                 source_sha), limit=4096)
    except LLMToolError:
        _stop('D6_TASK_BINDING_MISMATCH')
    if (type(artifact) is not str or type(artifact_sha256) is not str or
            hashlib.sha256(artifact.encode('utf-8')).hexdigest() != artifact_sha256 or
            artifact.encode('utf-8') != live['diff']):
        _stop('WORKTREE_ARTIFACT_MISMATCH')
    return {
        'task_id': task_binding['task_id'], 'run_id': task_binding['run_id'],
        'executor_task_id': registration.task_id,
        'executor_identity': registration.trusted_agent_id,
        'executor_session_ref': registration.session_ref,
        'executor_native_session_id': registration.native_session_id,
        'executor_native_session': ('VERIFIED' if registration.native_session_id else 'UNKNOWN'),
        'root': registration.root, 'common_dir': registration.common_dir,
        'branch': registration.branch, 'source_sha': source_sha,
        'result_head': None, 'result_mode': 'UNCOMMITTED_WORKTREE',
        'changed_paths': live['changed_paths'], 'diff_sha256': live['diff_sha256'],
        'status_sha256': live['status_sha256'],
        'file_manifest_sha256': live['file_manifest_sha256'],
    }


def _tree_entry(root, tree_sha, path):
    raw = _git(root, ('ls-tree', '-z', tree_sha, '--', path), limit=4096)
    if not raw:
        return None
    if not raw.endswith(b'\0') or raw.count(b'\0') != 1:
        _stop('DEVELOPER_ARTIFACT_CHECK_UNKNOWN')
    metadata, entry_path = raw[:-1].split(b'\t', 1)
    fields = metadata.split(b' ')
    if (len(fields) != 3 or entry_path != path.encode('utf-8') or
            fields[1] != b'blob' or fields[0] not in (b'100644', b'100755') or
            re.fullmatch(rb'[0-9a-f]{40}', fields[2]) is None):
        _stop('DEVELOPER_ARTIFACT_TREE_ENTRY_UNSUPPORTED')
    return fields[0].decode('ascii'), fields[2].decode('ascii')


def _tree_blob(root, tree_sha, path):
    entry = _tree_entry(root, tree_sha, path)
    if entry is None:
        return None
    mode, blob_sha = entry
    return mode, _git(root, ('cat-file', 'blob', blob_sha))


def _proposal_blob_size(root, tree_sha, path):
    entry = _tree_entry(root, tree_sha, path)
    if entry is None:
        return 0
    raw_size = _git(root, ('cat-file', '-s', entry[1]), limit=32).strip()
    if not re.fullmatch(rb'(0|[1-9][0-9]{0,12})', raw_size):
        _stop('DEVELOPER_ARTIFACT_CHECK_UNKNOWN')
    size = int(raw_size)
    if size > _GIT_LIMIT:
        _stop('DEVELOPER_ARTIFACT_BLOB_TOO_LARGE')
    return size


def verify_artifact_applied(root, artifact, artifact_sha256, source_sha, result_sha,
                            changed_paths, write_paths):
    """Apply the proposal in a bounded temp tree and compare result blobs/modes."""
    if (type(artifact) is not str or not artifact or
            len(artifact.encode('utf-8')) > _GIT_LIMIT or
            type(artifact_sha256) is not str or
            hashlib.sha256(artifact.encode('utf-8')).hexdigest() != artifact_sha256 or
            type(source_sha) is not str or _HEX40.fullmatch(source_sha) is None or
            type(result_sha) is not str or _HEX40.fullmatch(result_sha) is None):
        _stop('DEVELOPER_ARTIFACT_BINDING_INVALID')
    patch_paths = []
    for line in artifact.splitlines():
        if line.startswith('diff --git '):
            match = re.fullmatch(r'diff --git a/([^\s]+) b/([^\s]+)', line)
            if match is None or match.group(1) != match.group(2):
                _stop('DEVELOPER_ARTIFACT_INVALID')
            patch_paths.append(match.group(1))
    if (not patch_paths or len(patch_paths) > _PROPOSAL_FILE_LIMIT or
            len(patch_paths) != len(set(patch_paths)) or
            set(patch_paths) != set(changed_paths) or
            not set(patch_paths).issubset(set(write_paths))):
        _stop('DEVELOPER_ARTIFACT_RESULT_MISMATCH')
    for path in patch_paths:
        parts = Path(path).parts
        if (Path(path).is_absolute() or not parts or
                any(part in ('', '.', '..') for part in parts) or '\\' in path or
                '\x00' in path):
            _stop('DEVELOPER_ARTIFACT_INVALID')
    total_blob_bytes = 0
    for path in patch_paths:
        total_blob_bytes += _proposal_blob_size(root, source_sha, path)
        total_blob_bytes += _proposal_blob_size(root, result_sha, path)
        if total_blob_bytes > _PROPOSAL_TREE_LIMIT:
            _stop('DEVELOPER_ARTIFACT_TREE_TOO_LARGE')
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_ATTR_NOSYSTEM': '1', 'GIT_OPTIONAL_LOCKS': '0',
           'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_LAZY_FETCH': '1'}
    try:
        with tempfile.TemporaryDirectory(prefix='llmtool-proposal-tree-') as temporary:
            temp_root = Path(temporary)
            for path in patch_paths:
                source = _tree_blob(root, source_sha, path)
                if source is None:
                    continue
                mode, content = source
                target = temp_root.joinpath(*Path(path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream:
                    stream.write(content)
                target.chmod(0o755 if mode == '100755' else 0o644)
            proc = subprocess.run(
                ['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', 'apply',
                 '--binary', '--whitespace=nowarn', '-'],
                cwd=temp_root, input=artifact.encode('utf-8'),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_TIMEOUT, env=env, shell=False, check=False)
            if proc.returncode != 0:
                _stop('DEVELOPER_ARTIFACT_NOT_APPLIED')
            for path in patch_paths:
                expected = _tree_blob(root, result_sha, path)
                target = temp_root.joinpath(*Path(path).parts)
                try:
                    actual_stat = target.lstat()
                except FileNotFoundError:
                    actual_stat = None
                if expected is None:
                    if actual_stat is not None:
                        _stop('DEVELOPER_ARTIFACT_RESULT_TREE_MISMATCH')
                    continue
                mode, content = expected
                if (actual_stat is None or not stat.S_ISREG(actual_stat.st_mode) or
                        target.read_bytes() != content or
                        bool(actual_stat.st_mode & 0o111) != (mode == '100755')):
                    _stop('DEVELOPER_ARTIFACT_RESULT_TREE_MISMATCH')
    except LLMToolError:
        raise
    except Exception:
        _stop('DEVELOPER_ARTIFACT_CHECK_UNKNOWN')


def run_bounded_tests(*, root, result_sha, read_paths, write_paths):
    """Run only fixed unittest discovery for test files in the stored task scope."""
    eligible = sorted({p for p in (*read_paths, *write_paths)
                       if type(p) is str and _TEST_PATH.fullmatch(p)})
    if not eligible:
        _stop('TEST_SCOPE_MISSING')
    before = collect_git_snapshot(root)
    if before.head != result_sha or not before.clean:
        _stop('TEST_SOURCE_MISMATCH')
    evidence = []
    with tempfile.TemporaryDirectory(prefix='llmtool-task-tests-') as home:
        for path in eligible:
            env = {'PATH': os.path.dirname(sys.executable) + ':/usr/bin:/bin',
                   'LC_ALL': 'C', 'HOME': home,
                   'PYTHONPATH': str(Path(root) / 'tools' / 'llmtool'),
                   'PYTHONDONTWRITEBYTECODE': '1'}
            argv = [sys.executable, '-B', '-m', 'unittest', 'discover',
                    '-s', 'tools/llmtool/tests', '-p', Path(path).name]
            try:
                process = subprocess.Popen(argv, cwd=root, env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, shell=False, close_fds=True,
                    start_new_session=True)
            except OSError:
                _stop('TEST_RUNNER_UNAVAILABLE')
            output = bytearray()
            deadline = time.monotonic() + _TEST_TIMEOUT
            selector = selectors.DefaultSelector()
            started = time.monotonic()
            try:
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        _stop('TEST_RUNNER_TIMEOUT')
                    for key, _ in selector.select(min(remaining, .1)):
                        chunk = os.read(key.fileobj.fileno(), 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            if len(output) + len(chunk) > _TEST_OUTPUT_LIMIT:
                                _stop('TEST_OUTPUT_TOO_LARGE')
                            output.extend(chunk)
                code = process.wait(timeout=max(0, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                _stop('TEST_RUNNER_TIMEOUT')
            finally:
                selector.close()
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, 9)
                    except OSError:
                        process.kill()
                    process.wait()
                process.stdout.close()
            evidence.append({
                'path': path, 'source_sha': result_sha, 'exit_code': code,
                'output_sha256': hashlib.sha256(output).hexdigest(),
                'duration_ms': max(0, int((time.monotonic()-started)*1000)),
                'command_sha256': hashlib.sha256(json.dumps(argv, separators=(',', ':')).encode()).hexdigest(),
            })
    after = collect_git_snapshot(root)
    if after != before:
        _stop('TEST_SOURCE_CHANGED')
    return evidence


def run_bounded_worktree_tests(*, root, source_sha, diff_sha256,
                               changed_paths, read_paths, write_paths):
    """Run fixed unittest discovery against an exact, still-uncommitted D6 diff."""
    if (type(diff_sha256) is not str or _HEX64.fullmatch(diff_sha256) is None or
            type(changed_paths) is not list or not changed_paths):
        _stop('TEST_BINDING_INVALID')
    eligible = sorted({p for p in (*read_paths, *write_paths)
                       if type(p) is str and _TEST_PATH.fullmatch(p)})
    if not eligible:
        _stop('TEST_SCOPE_MISSING')
    before = _worktree_snapshot(root=root, source_sha=source_sha,
                                read_paths=read_paths, write_paths=write_paths)
    if (before['diff_sha256'] != diff_sha256 or
            before['changed_paths'] != changed_paths):
        _stop('TEST_SOURCE_MISMATCH')
    evidence = []
    with tempfile.TemporaryDirectory(prefix='llmtool-task-worktree-tests-') as home:
        for path in eligible:
            env = {'PATH': os.path.dirname(sys.executable) + ':/usr/bin:/bin',
                   'LC_ALL': 'C', 'HOME': home,
                   'PYTHONPATH': str(Path(root) / 'tools' / 'llmtool'),
                   'PYTHONDONTWRITEBYTECODE': '1'}
            argv = [sys.executable, '-B', '-m', 'unittest', 'discover',
                    '-s', 'tools/llmtool/tests', '-p', Path(path).name]
            try:
                process = subprocess.Popen(argv, cwd=root, env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, shell=False, close_fds=True,
                    start_new_session=True)
            except OSError:
                _stop('TEST_RUNNER_UNAVAILABLE')
            output = bytearray()
            deadline = time.monotonic() + _TEST_TIMEOUT
            selector = selectors.DefaultSelector()
            started = time.monotonic()
            try:
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        _stop('TEST_RUNNER_TIMEOUT')
                    for key, _ in selector.select(min(remaining, .1)):
                        chunk = os.read(key.fileobj.fileno(), 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            if len(output) + len(chunk) > _TEST_OUTPUT_LIMIT:
                                _stop('TEST_OUTPUT_TOO_LARGE')
                            output.extend(chunk)
                code = process.wait(timeout=max(0, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                _stop('TEST_RUNNER_TIMEOUT')
            finally:
                selector.close()
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, 9)
                    except OSError:
                        process.kill()
                    process.wait()
                process.stdout.close()
            evidence.append({
                'path': path, 'source_sha': source_sha, 'result_head': None,
                'diff_sha256': diff_sha256, 'exit_code': code,
                'output_sha256': hashlib.sha256(output).hexdigest(),
                'duration_ms': max(0, int((time.monotonic()-started)*1000)),
                'command_sha256': hashlib.sha256(json.dumps(argv, separators=(',', ':')).encode()).hexdigest(),
            })
    after = _worktree_snapshot(root=root, source_sha=source_sha,
                               read_paths=read_paths, write_paths=write_paths)
    if after != before:
        _stop('TEST_SOURCE_CHANGED')
    return evidence


def _github_get(path):
    url = 'https://api.github.com/repos/davidcatcat/davidcattoolsproject/' + path
    request = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'llmtool-task-completion/1',
        'X-GitHub-Api-Version': '2022-11-28',
    }, method='GET')
    # Do not forward ambient credentials or proxy credentials from this process.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=8) as response:
            if response.status != 200:
                _stop('GITHUB_EVIDENCE_UNAVAILABLE')
            raw = response.read(1024 * 1024 + 1)
    except LLMToolError:
        raise
    except Exception:
        _stop('GITHUB_EVIDENCE_UNAVAILABLE')
    if len(raw) > 1024 * 1024:
        _stop('GITHUB_EVIDENCE_TOO_LARGE')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                          parse_constant=lambda _: _stop('GITHUB_EVIDENCE_INVALID'))
    except Exception:
        _stop('GITHUB_EVIDENCE_INVALID')


def _review_time(review):
    submitted = review.get('submitted_at')
    if type(submitted) is not str:
        _stop('REVIEW_EVIDENCE_INVALID')
    try:
        parsed = datetime.fromisoformat(submitted.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            _stop('REVIEW_EVIDENCE_INVALID')
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        _stop('REVIEW_EVIDENCE_INVALID')


def _review_binding_line(task_id, run_id, contract_digest, result_sha):
    return (f'LLMTOOL-REVIEW-BINDING v1 task={task_id} run={run_id} '
            f'contract={contract_digest} head={result_sha}')


def _collect_ci_evidence(*, pr_number, result_sha):
    workflow = _github_get('actions/workflows/llmtool-ci.yml')
    if (type(workflow) is not dict or type(workflow.get('id')) is not int or
            workflow['id'] <= 0 or workflow.get('path') != _CI_WORKFLOW_PATH or
            workflow.get('name') != _CI_WORKFLOW_NAME or workflow.get('state') != 'active'):
        _stop('CI_WORKFLOW_MISMATCH')
    workflow_id = workflow['id']
    runs = _github_get(
        f'actions/workflows/{workflow_id}/runs?head_sha={result_sha}&event=pull_request&per_page=100')
    if (type(runs) is not dict or type(runs.get('workflow_runs')) is not list or
            type(runs.get('total_count')) is not int or
            runs['total_count'] != len(runs['workflow_runs']) or
            len(runs['workflow_runs']) > 100):
        _stop('CI_WORKFLOW_RUNS_INVALID')
    candidates = []
    for run in runs['workflow_runs']:
        if type(run) is not dict:
            _stop('CI_WORKFLOW_RUNS_INVALID')
        pull_requests = run.get('pull_requests')
        if (run.get('workflow_id') != workflow_id or run.get('path') != _CI_WORKFLOW_PATH or
                run.get('event') != 'pull_request' or run.get('head_sha') != result_sha or
                type(pull_requests) is not list or
                not any(type(item) is dict and item.get('number') == pr_number and
                        type(item.get('head')) is dict and
                        item['head'].get('sha') == result_sha for item in pull_requests)):
            _stop('CI_WORKFLOW_RUN_MISMATCH')
        if (type(run.get('id')) is not int or run['id'] <= 0 or
                type(run.get('run_number')) is not int or run['run_number'] <= 0 or
                type(run.get('run_attempt')) is not int or run['run_attempt'] <= 0):
            _stop('CI_WORKFLOW_RUNS_INVALID')
        candidates.append(run)
    if not candidates:
        return {'workflow_id': workflow_id, 'run_id': None, 'run_attempt': None,
                'run_number': None, 'successful_checks': [], 'check_ids': []}
    run = max(candidates, key=lambda item: (item['run_number'], item['run_attempt'], item['id']))
    run_id, attempt = run['id'], run['run_attempt']
    jobs = _github_get(f'actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100')
    if (type(jobs) is not dict or type(jobs.get('jobs')) is not list or
            type(jobs.get('total_count')) is not int or
            jobs['total_count'] != len(jobs['jobs']) or len(jobs['jobs']) > 100):
        _stop('CI_WORKFLOW_JOBS_INVALID')
    required_jobs = {}
    for job in jobs['jobs']:
        if type(job) is not dict:
            _stop('CI_WORKFLOW_JOBS_INVALID')
        name = job.get('name')
        if name not in _REQUIRED_CHECKS:
            continue
        check_url = job.get('check_run_url')
        check_id_match = (None if type(check_url) is not str else re.fullmatch(
            r'https://api\.github\.com/repos/davidcatcat/davidcattoolsproject/'
            r'check-runs/([1-9][0-9]{0,18})', check_url))
        if (name in required_jobs or job.get('run_id') != run_id or
                type(job.get('id')) is not int or job['id'] <= 0 or
                check_id_match is None):
            _stop('CI_WORKFLOW_JOBS_INVALID')
        required_jobs[name] = (job, int(check_id_match.group(1)))
    if set(required_jobs) != set(_REQUIRED_CHECKS):
        return {'workflow_id': workflow_id, 'run_id': run_id, 'run_attempt': attempt,
                'run_number': run['run_number'], 'successful_checks': [], 'check_ids': []}

    checks = _github_get(f'commits/{result_sha}/check-runs?per_page=100')
    if (type(checks) is not dict or type(checks.get('check_runs')) is not list or
            type(checks.get('total_count')) is not int or
            checks['total_count'] != len(checks['check_runs']) or
            len(checks['check_runs']) > 100):
        _stop('CI_EVIDENCE_INVALID')
    by_id = {}
    for check in checks['check_runs']:
        if type(check) is not dict or type(check.get('id')) is not int:
            _stop('CI_EVIDENCE_INVALID')
        if check['id'] in by_id and by_id[check['id']] != check:
            _stop('CI_EVIDENCE_INVALID')
        by_id[check['id']] = check
    successful = set()
    check_ids = {}
    for name, (job, check_id) in required_jobs.items():
        check = by_id.get(check_id)
        app = None if check is None else check.get('app')
        if (check is None or check.get('name') != name or
                check.get('head_sha') != result_sha or
                type(app) is not dict or app.get('id') != _GITHUB_ACTIONS_APP_ID or
                app.get('slug') != 'github-actions' or app.get('name') != 'GitHub Actions'):
            continue
        if (job.get('status') == 'completed' and job.get('conclusion') == 'success' and
                check.get('status') == 'completed' and check.get('conclusion') == 'success'):
            successful.add(name)
            check_ids[name] = check['id']
    run_success = run.get('status') == 'completed' and run.get('conclusion') == 'success'
    if not run_success:
        successful.clear()
    return {'workflow_id': workflow_id, 'run_id': run_id, 'run_attempt': attempt,
            'run_number': run['run_number'], 'successful_checks': sorted(successful),
            'check_ids': check_ids}


def collect_github_evidence(*, pr_number, result_sha, task_id, run_id,
                            contract_digest, executor_identity,
                            executor_session_ref, executor_native_session_id,
                            reviewer_task_id, common_dir):
    """Read a public PR, formal review marker and exact-head required check runs."""
    if (type(pr_number) is not int or not 1 <= pr_number <= 9999999 or
            type(result_sha) is not str or _HEX40.fullmatch(result_sha) is None or
            type(task_id) is not str or re.fullmatch(r'[0-9a-f]{32}', task_id) is None or
            type(run_id) is not str or re.fullmatch(r'[0-9a-f]{32}', run_id) is None or
            type(contract_digest) is not str or _HEX64.fullmatch(contract_digest) is None):
        _stop('PR_NUMBER_INVALID')
    pr = _github_get(f'pulls/{pr_number}')
    if (type(pr) is not dict or pr.get('state') != 'open' or pr.get('merged') is not False or
            type(pr.get('head')) is not dict or pr['head'].get('sha') != result_sha or
            type(pr.get('base')) is not dict or
            type(pr['base'].get('repo')) is not dict or
            pr['base']['repo'].get('full_name') != 'davidcatcat/davidcattoolsproject' or
            type((pr.get('user') or {}).get('login')) is not str):
        _stop('PR_HEAD_MISMATCH')
    reviews = _github_get(f'pulls/{pr_number}/reviews?per_page=100')
    if type(reviews) is not list or len(reviews) >= 100 or any(
            type(review) is not dict for review in reviews):
        _stop('REVIEW_EVIDENCE_INVALID')
    try:
        reviewer = read_d6_registration(reviewer_task_id, common_dir,
                                        allowed_states=_D6_EVIDENCE_STATES)
    except LLMToolError:
        _stop('REVIEWER_D6_REGISTRY_UNAVAILABLE')
    latest = {}
    disposition_states = {'APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'}
    for candidate in reviews:
        if candidate.get('commit_id') != result_sha or candidate.get('state') not in disposition_states:
            continue
        login = (candidate.get('user') or {}).get('login')
        if type(login) is not str or not login:
            _stop('REVIEW_EVIDENCE_INVALID')
        submitted_at = _review_time(candidate)
        key = login.casefold()
        previous = latest.get(key)
        if previous is not None and submitted_at == previous[0]:
            if candidate != previous[1]:
                _stop('REVIEW_EVIDENCE_INVALID')
            continue
        if previous is None or submitted_at > previous[0]:
            latest[key] = (submitted_at, candidate)
    reviewer_login = reviewer.github_login
    review = None if reviewer_login is None else latest.get(reviewer_login.casefold(), (None, None))[1]
    formal_marker = re.compile(r'(?im)^\s*FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\s*$')
    review_body = '' if review is None or type(review.get('body')) is not str else review['body']
    body_lines = {line.strip() for line in review_body.splitlines()}
    review_binding_verified = _review_binding_line(
        task_id, run_id, contract_digest, result_sha) in body_lines
    missing = []
    if review is None:
        missing.append('FORMAL_REVIEW_MISSING')
    elif review.get('state') != 'APPROVED' or not formal_marker.search(review_body):
        missing.append('FORMAL_REVIEW_NOT_ACCEPTED')
    if review is not None and not review_binding_verified:
        missing.append('FORMAL_REVIEW_BINDING_MISMATCH')
    if any(item[1].get('state') == 'CHANGES_REQUESTED' for item in latest.values()):
        missing.append('FORMAL_REVIEW_NOT_ACCEPTED')
    if (reviewer.trusted_agent_id == executor_identity or
            reviewer.session_ref == 'UNKNOWN' or reviewer.session_ref == '' or
            reviewer.session_ref == executor_session_ref):
        missing.append('REVIEWER_D6_SESSION_NOT_INDEPENDENT')
    # Public GitHub review proves actor/body/head, but cannot bind that actor to
    # a native Reviewer session unless the Integrator D6 record pins the login.
    if reviewer.native_session_id in (None, 'UNKNOWN'):
        missing.append('REVIEWER_NATIVE_SESSION_UNVERIFIED')
    elif executor_native_session_id in (None, 'UNKNOWN'):
        missing.append('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT')
    elif reviewer.native_session_id == executor_native_session_id:
        missing.append('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT')
    if reviewer.github_login is None:
        missing.append('REVIEWER_ACTOR_D6_BINDING_MISSING')
    elif reviewer_login is not None and reviewer_login.casefold() != reviewer.github_login.casefold():
        missing.append('REVIEWER_ACTOR_D6_BINDING_MISMATCH')
    try:
        reviewer_snapshot = collect_git_snapshot(reviewer.root)
        if (reviewer_snapshot.common_dir != reviewer.common_dir or
                reviewer_snapshot.branch != reviewer.branch or
                reviewer_snapshot.head != result_sha):
            missing.append('REVIEWER_D6_HEAD_MISMATCH')
    except LLMToolError:
        missing.append('REVIEWER_D6_HEAD_UNAVAILABLE')
    reviewer_gates = {
        'FORMAL_REVIEW_MISSING', 'FORMAL_REVIEW_NOT_ACCEPTED',
        'REVIEWER_D6_SESSION_NOT_INDEPENDENT',
        'REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT',
        'REVIEWER_NATIVE_SESSION_UNVERIFIED',
        'FORMAL_REVIEW_BINDING_MISMATCH',
        'REVIEWER_ACTOR_D6_BINDING_MISSING',
        'REVIEWER_ACTOR_D6_BINDING_MISMATCH',
        'REVIEWER_D6_HEAD_MISMATCH', 'REVIEWER_D6_HEAD_UNAVAILABLE',
    }
    reviewer_bound = (review is not None and review.get('state') == 'APPROVED' and
                      bool(formal_marker.search(review_body)) and review_binding_verified and
                      not reviewer_gates.intersection(missing))
    ci = _collect_ci_evidence(pr_number=pr_number, result_sha=result_sha)
    successful = set(ci['successful_checks'])
    if not _REQUIRED_CHECKS.issubset(successful):
        missing.append('REQUIRED_CI_MISSING')
    return {'evidence': {'pr_number': pr_number, 'head_sha': result_sha,
            'formal_review_state': 'ACCEPTED' if reviewer_bound else 'UNVERIFIED',
            'review_actor': None if review is None else review['user']['login'].casefold(),
            'reviewer_github_login': None if reviewer.github_login is None else reviewer.github_login.casefold(),
            'review_commit_sha': None if review is None else review['commit_id'],
            'reviewer_d6_identity': reviewer.trusted_agent_id,
            'reviewer_task_id': reviewer_task_id,
            'reviewer_session_ref': reviewer.session_ref,
            'review_session_identity': ('D6_NATIVE_SESSION_VERIFIED'
                if reviewer.native_session_id not in (None, 'UNKNOWN') else 'UNKNOWN'),
            'review_task_id': task_id, 'review_run_id': run_id,
            'review_contract_digest': contract_digest,
            'review_binding_verified': review_binding_verified,
            'required_checks': sorted(_REQUIRED_CHECKS),
            'successful_checks': sorted(successful), 'ci_head_sha': result_sha,
            'ci_workflow_path': _CI_WORKFLOW_PATH,
            'ci_workflow_name': _CI_WORKFLOW_NAME,
            'ci_workflow_id': ci['workflow_id'], 'ci_run_id': ci['run_id'],
            'ci_run_attempt': ci['run_attempt'], 'ci_run_number': ci['run_number'],
            'ci_check_ids': ci['check_ids']},
            'missing_gates': sorted(set(missing))}
