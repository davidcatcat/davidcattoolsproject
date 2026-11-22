"""Review handoff, receipt and verdict helpers for task_completion (#2281).

The code is moved verbatim. task_completion re-exports these names, and every
helper that stays there is resolved lazily through task_completion, so tests
that patch the task_completion module path keep their effect.
"""

from __future__ import annotations

import hashlib
import re

from .task_completion import (
    _FORMAL_ACCEPT,
    _HEX64,
    _PROPOSAL_FILE_LIMIT,
    _REVIEW_VERDICTS,
    _VERDICT_MARKER,
    _VERDICT_STATUS,
)


def _stop(*args, **kwargs):
    from . import task_completion as _m
    return _m._stop(*args, **kwargs)


def _git(*args, **kwargs):
    from . import task_completion as _m
    return _m._git(*args, **kwargs)


def _canonical_json_bytes(*args, **kwargs):
    from . import task_completion as _m
    return _m._canonical_json_bytes(*args, **kwargs)


def validate_review_dispatch_intent(*args, **kwargs):
    from . import task_completion as _m
    return _m.validate_review_dispatch_intent(*args, **kwargs)


def _review_binding_line(*args, **kwargs):
    from . import task_completion as _m
    return _m._review_binding_line(*args, **kwargs)


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
    if (type(verdict) is not str or type(decision) is not str or
            type(repair_round) is not int or repair_round not in (0, 1)):
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


def review_repair_intent(handoff, review_return):
    """Build a deterministic, source-bound request for the one allowed repair.

    This pure value is an idempotency envelope, not a Worker dispatch. A
    caller must separately enforce at-most-once consumption and recheck the
    original Task/Worker authority before any future execution.
    """
    if type(handoff) is not dict or set(handoff) != {
            'intent', 'dispatch_state', 'receipt', 'next_step', 'error_code'}:
        _stop('REVIEW_REPAIR_BINDING_INVALID')
    expected_handoff = review_handoff_record(handoff['intent'], handoff['receipt'])
    if (_canonical_json_bytes(expected_handoff, 'REVIEW_REPAIR_BINDING_INVALID') !=
            _canonical_json_bytes(handoff, 'REVIEW_REPAIR_BINDING_INVALID')):
        _stop('REVIEW_REPAIR_BINDING_INVALID')
    validate_review_return_record(review_return, handoff)
    intent = handoff['intent']
    receipt = handoff['receipt']
    recommendation = review_return['pm_recommendation']
    if (review_return['state'] != 'RETURNED' or
            review_return['pm_dispatch'] != 'RETURNED' or
            review_return['next_step'] != 'RETURN_TO_DEVELOPER_ONCE' or
            type(recommendation) is not dict or
            set(recommendation) != {'decision', 'summary'} or
            recommendation.get('decision') != 'RETURN_TO_DEVELOPER' or
            type(recommendation.get('summary')) is not str or
            not recommendation['summary'] or
            intent['repair_round'] != 0 or receipt['verdict'] != 'FAIL'):
        _stop('REVIEW_REPAIR_BINDING_INVALID')
    try:
        summary_bytes = recommendation['summary'].encode('utf-8')
    except UnicodeEncodeError:
        _stop('REVIEW_REPAIR_BINDING_INVALID')
    if len(summary_bytes) > 2048:
        _stop('REVIEW_REPAIR_BINDING_INVALID')
    payload = {
        'schema_version': 1,
        'task_id': intent['task_id'],
        'run_id': intent['run_id'],
        'contract_digest': intent['contract_digest'],
        'source_sha': intent['source_sha'],
        'result_head': intent['result_head'],
        'diff_sha256': intent['diff_sha256'],
        'changed_paths': list(intent['changed_paths']),
        'developer_task_id': intent['developer_task_id'],
        'developer_identity': intent['developer_identity'],
        'developer_session_ref': intent['developer_session_ref'],
        'review_dispatch_id': intent['dispatch_id'],
        'review_receipt_sha256': receipt['receipt_sha256'],
        'prior_repair_round': 0,
        'repair_round': 1,
        'decision': 'RETURN_TO_DEVELOPER',
        'state': 'REQUESTED',
    }
    canonical = _canonical_json_bytes(payload, 'REVIEW_REPAIR_BINDING_INVALID')
    payload['repair_intent_id'] = hashlib.sha256(
        b'llmtool/review-repair-intent/v1\0' + canonical).hexdigest()
    payload['intent_sha256'] = hashlib.sha256(_canonical_json_bytes(
        payload, 'REVIEW_REPAIR_BINDING_INVALID')).hexdigest()
    return payload


def validate_review_repair_intent(repair_intent, handoff, review_return):
    """Validate an envelope against its exact persisted Reviewer/PM inputs."""
    fields = {
        'schema_version', 'task_id', 'run_id', 'contract_digest', 'source_sha',
        'result_head', 'diff_sha256', 'changed_paths', 'developer_task_id',
        'developer_identity', 'developer_session_ref', 'review_dispatch_id',
        'review_receipt_sha256', 'prior_repair_round', 'repair_round',
        'decision', 'state', 'repair_intent_id', 'intent_sha256',
    }
    if type(repair_intent) is not dict or set(repair_intent) != fields:
        _stop('REVIEW_REPAIR_BINDING_INVALID')
    value = dict(repair_intent)
    claimed_digest = value.pop('intent_sha256')
    if type(claimed_digest) is not str or _HEX64.fullmatch(claimed_digest) is None:
        _stop('REVIEW_REPAIR_DIGEST_MISMATCH')
    actual_digest = hashlib.sha256(_canonical_json_bytes(
        value, 'REVIEW_REPAIR_BINDING_INVALID')).hexdigest()
    if claimed_digest != actual_digest:
        _stop('REVIEW_REPAIR_DIGEST_MISMATCH')
    expected = review_repair_intent(handoff, review_return)
    if (_canonical_json_bytes(expected, 'REVIEW_REPAIR_BINDING_INVALID') !=
            _canonical_json_bytes(repair_intent, 'REVIEW_REPAIR_BINDING_INVALID')):
        _stop('REVIEW_REPAIR_BINDING_MISMATCH')
    return repair_intent


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


def _committed_changed_paths(root, source_sha, result_sha):
    """Read all paths changed between the task source and exact result."""
    raw = _git(root, ('diff', '--name-only', '-z', '--no-ext-diff', '--no-textconv',
                      '--no-renames', source_sha, result_sha, '--'), limit=1024 * 1024)
    try:
        paths = sorted(set(path for path in raw.decode('utf-8', 'strict').split('\x00') if path))
    except UnicodeError:
        _stop('TEST_PATH_INVALID')
    if not paths or len(paths) > _PROPOSAL_FILE_LIMIT:
        _stop('TEST_PATH_INVALID')
    return paths


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
    if type(intent) is not dict or type(receipt) is not dict:
        _stop('REVIEW_RECEIPT_INVALID')
    schema_version = receipt.get('schema_version')
    legacy = schema_version == 1 and set(receipt) == fields
    current = (schema_version == 2 and
               set(receipt) == fields | {'review_packet_digest'})
    if not (legacy or current):
        _stop('REVIEW_RECEIPT_INVALID')
    validate_review_dispatch_intent(intent)
    value = dict(receipt)
    claimed = value.pop('receipt_sha256')
    actual = hashlib.sha256(_canonical_json_bytes(
        value, 'REVIEW_RECEIPT_INVALID')).hexdigest()
    if claimed != actual:
        _stop('REVIEW_RECEIPT_DIGEST_MISMATCH')
    packet_digest = None if legacy else receipt['review_packet_digest']
    if (type(schema_version) is not int or
            any(receipt.get(key) != intent.get(key) for key in (
                'dispatch_id', 'task_id', 'run_id', 'contract_digest', 'source_sha',
                'result_head', 'diff_sha256', 'developer_task_id')) or
            type(receipt['verdict']) is not str or receipt['verdict'] not in _REVIEW_VERDICTS or
            type(receipt['security']) is not str or receipt['security'] not in _REVIEW_VERDICTS or
            type(receipt['functional']) is not str or receipt['functional'] not in _REVIEW_VERDICTS or
            type(receipt['execution_status']) is not str or
            receipt['execution_status'] not in ('SUCCEEDED', 'FAILED', 'BLOCKED',
                                                 'UNKNOWN', 'NOT_DISPATCHED') or
            (packet_digest is not None and
             (type(packet_digest) is not str or _HEX64.fullmatch(packet_digest) is None)) or
            (current and receipt['execution_status'] == 'SUCCEEDED' and
             packet_digest is None) or
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
                    'findings_sha256')) or packet_digest is not None):
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






