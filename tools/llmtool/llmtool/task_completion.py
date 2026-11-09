"""Fail-closed evidence collection for an already completed D6 Executor task.

This module never applies a patch or writes Git state. A D6 registry record is a
cooperative local provenance source; it is not a same-UID security boundary.

Stable Reviewer completion codes: ``REVIEW_DISPATCH_BINDING_INVALID`` covers
invalid dispatch intents; ``REVIEW_RECEIPT_INVALID``,
``REVIEW_RECEIPT_BINDING_MISMATCH``, and ``REVIEW_RECEIPT_DIGEST_MISMATCH``
cover invalid or mismatched handoff/return receipts; ``REVIEW_REPAIR_BINDING_INVALID``,
``REVIEW_REPAIR_BINDING_MISMATCH``, and ``REVIEW_REPAIR_DIGEST_MISMATCH`` cover
invalid or mismatched deterministic repair-intent envelopes. JSON-bound values
are restricted to finite JSON scalars and containers no deeper than 64 levels.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

from . import source_freshness
from . import trust
from .errors import LLMToolError
from .test_output_excerpts import select_test_excerpts

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
# Legacy CI values, kept only for ``llmtool trust verify-legacy`` until #369
# PR-10. The completion gate reads ``ci_trust`` from trust.json at use time
# (see ``_ci_trust``); none of these is consulted by the runtime CI checks,
# except that ``_GITHUB_ACTIONS_APP_ID`` stays the fixed pin the trust value
# ``github_actions_app_id`` must equal (the trust schema pins it too).
_MAC_CHECK = 'check (macOS 3.13)'
_LINUX_CHECK = 'check (Linux 3.14)'
_DOCS_CHECK = 'check (docs)'
_REQUIRED_CHECKS = frozenset((_MAC_CHECK, _LINUX_CHECK))
_NOT_RUN_CHECKS = ('Linux Python 3.9', 'Windows CI')
_CI_RUNNER_NAMES = frozenset(('davidproject-self-action', 'davidproject-self-action-2'))
_CI_WORKFLOW_PATH = '.github/workflows/llmtool-mac-ci.yml'
_CI_WORKFLOW_NAME = 'llmtool Mac CI'
_LINUX_CI_WORKFLOW_PATH = '.github/workflows/llmtool-linux-ci.yml'
_LINUX_CI_WORKFLOW_NAME = 'llmtool Linux CI'
_LINUX_CI_RUNNER_NAMES = frozenset((
    'ec2-self-runner', 'ec2-self-runner-2', 'ec2-self-runner-3',
    'ec2-self-runner-4'))
_GITHUB_ACTIONS_APP_ID = 15368
_D6_EVIDENCE_STATES = frozenset(('ACTIVE', 'REVIEW_PENDING', 'REPAIR'))
# A GitHub-hosted job reports this runner group; self-hosted groups never do.
_GITHUB_HOSTED_RUNNER_GROUP = 'GitHub Actions'
_TEST_REGISTRY = (
    (re.compile(r'tools/llmtool/tests/test_[A-Za-z0-9_]+\.py\Z'), 'unittest'),
    (re.compile(r'scripts/test_[A-Za-z0-9_]+\.py\Z'), 'script'),
)
_REVIEW_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,95}\Z')
_REVIEW_VERDICTS = frozenset(('PASS', 'FAIL', 'BLOCKED'))
_GH_PATH = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'
# The leading `<!-- activity: ... -->` line every verdict post starts with.
_VERDICT_ACTIVITY = re.compile(r'<!-- activity: (?:[^>]* )?type=review_verdict(?: [^>]*)? -->')
# The one standard formal verdict format: skills/ai-team-delivery/formal-verdict.md.
_VERDICT_MARKER = re.compile(
    r'<!-- activity: type=review_verdict actor=(Claude|Codex) target=PM status=(PASS|FIX|FAIL) -->')
_VERDICT_STATUS = re.compile(
    r'Verdict: (PASS|FIX|FAIL) \| Version: v[1-9][0-9]{0,3} \| HEAD: ([0-9a-f]{40})')
_FORMAL_ACCEPT = 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT'
_REVIEW_VERDICT_ACTIVITY_LINE = '<!-- activity: type=review_verdict actor=Claude target=PM status=PASS -->'
_GITHUB_API_PATH = re.compile(
    r'(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?:compare/[0-9a-f]{40}\.\.\.[0-9a-f]{40}|'
    r'(?!compare/)[A-Za-z0-9_./?=&-]{1,256})\Z')


def _stop(code):
    error = LLMToolError('Executor completion evidence is unavailable; details omitted')
    error.completion_code = code
    raise error from None


def _canonical_json_bytes(value, code):
    """Serialize a bounded JSON tree, mapping only encoding failures to code."""
    stack = [(value, 0, False)]
    ancestors = set()
    while stack:
        item, depth, leaving = stack.pop()
        if leaving:
            ancestors.remove(id(item))
            continue
        if depth > 64:
            _stop(code)
        item_type = type(item)
        if item_type is dict or item_type is list:
            identity = id(item)
            if identity in ancestors:
                _stop(code)
            ancestors.add(identity)
            stack.append((item, depth, True))
            if item_type is dict:
                entries = list(item.items())
                if any(type(key) is not str for key, _ in entries):
                    _stop(code)
                stack.extend((child, depth + 1, False)
                             for _, child in reversed(entries))
            else:
                stack.extend((child, depth + 1, False)
                             for child in reversed(item))
        elif item_type is float:
            if not math.isfinite(item):
                _stop(code)
        elif item is None or item_type in (str, int, bool):
            continue
        else:
            _stop(code)
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        _stop(code)


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


def validate_env2_pass_marker(registration, *, expected_root, current_head):
    """Require the owner-only registry marker and report for this exact D6 HEAD."""
    marker = registration.env2_pass_marker
    if marker is None:
        _stop('D6_ENV2_PASS_MARKER_MISSING')
    required = {
        'schema_version', 'result', 'task_id', 'root_realpath',
        'submitted_head_sha', 'current_head_sha', 'environment_fingerprint',
        'tooling_sha', 'report_sha256',
    }
    fingerprint_re = re.compile(
        r'[a-z][a-z0-9]*\d+\.\d+-[a-z0-9][a-z0-9._-]{0,31}-'
        r'[a-z0-9][a-z0-9._-]{0,31}-[0-9a-f]{12}\Z')
    if type(marker) is not dict or set(marker) != required:
        _stop('D6_ENV2_PASS_MARKER_INVALID')
    if (type(marker['schema_version']) is not int or marker['schema_version'] != 1 or
            marker['result'] != 'PASS' or marker['task_id'] != registration.task_id or
            marker['root_realpath'] != expected_root or expected_root != registration.root or
            type(current_head) is not str or _HEX40.fullmatch(current_head) is None or
            marker['submitted_head_sha'] != current_head or
            marker['current_head_sha'] != current_head or
            current_head != registration.result_sha or
            marker['tooling_sha'] != (registration.env2_approved_tooling_sha or
                                      registration.source_sha) or
            type(marker['tooling_sha']) is not str or
            _HEX40.fullmatch(marker['tooling_sha']) is None or
            type(marker['environment_fingerprint']) is not str or
            fingerprint_re.fullmatch(marker['environment_fingerprint']) is None or
            type(marker['report_sha256']) is not str or
            _HEX64.fullmatch(marker['report_sha256']) is None):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    report_path = Path(expected_root) / 'tools' / 'llmtool' / '.d6-bootstrap-report.json'
    for directory in (Path(expected_root) / 'tools', Path(expected_root) / 'tools' / 'llmtool'):
        try:
            info = directory.lstat()
        except OSError:
            _stop('D6_ENV2_PASS_MARKER_MISMATCH')
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    try:
        before = report_path.lstat()
        if (not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or
                before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600 or
                before.st_nlink != 1 or before.st_size > 2 * 1024 * 1024):
            _stop('D6_ENV2_PASS_MARKER_MISMATCH')
        fd = os.open(report_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(fd)
            raw = bytearray()
            while len(raw) <= 2 * 1024 * 1024:
                chunk = os.read(fd, min(65536, 2 * 1024 * 1024 + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(fd)
            current = report_path.lstat()
        finally:
            os.close(fd)
    except LLMToolError:
        raise
    except Exception:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if (len(raw) > 2 * 1024 * 1024 or identity(before) != identity(opened) or
            identity(opened) != identity(after) or
            (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino) or
            hashlib.sha256(raw).hexdigest() != marker['report_sha256']):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    try:
        report = json.loads(bytes(raw).decode('utf-8'), object_pairs_hook=_unique_pairs,
                            parse_constant=lambda _: _stop('D6_ENV2_PASS_MARKER_INVALID'))
    except LLMToolError:
        raise
    except Exception:
        _stop('D6_ENV2_PASS_MARKER_INVALID')
    if (type(report) is not dict or report.get('result') != 'PASS' or
            report.get('task_id') != registration.task_id or
            report.get('root_realpath') != registration.root or
            report.get('head_sha') != current_head or
            report.get('tooling_sha') != marker['tooling_sha'] or
            report.get('cache_fingerprint') != marker['environment_fingerprint']):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    return marker


def _git(root, args, *, limit=_GIT_LIMIT, allow_exit_1=False,
         return_exit_code=False, env_overrides=None):
    """Run one fixed Git read with bounded output and no inherited Git config."""
    env = {
        'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
        'TMPDIR': os.environ.get('TMPDIR') or '/tmp',
        'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_ATTR_NOSYSTEM': '1',
        'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_LAZY_FETCH': '1',
        'GIT_NO_REPLACE_OBJECTS': '1',
        'GIT_TERMINAL_PROMPT': '0', 'GIT_PAGER': 'cat', 'PAGER': 'cat',
        'GIT_LITERAL_PATHSPECS': '1', 'TMPDIR': '/tmp',
    }
    if env_overrides is not None:
        if (type(env_overrides) is not dict or
                any(type(key) is not str or type(value) is not str
                    for key, value in env_overrides.items())):
            _stop('GIT_INSPECTION_FAILED')
        env.update(env_overrides)
    safe = ('-c', 'core.useReplaceRefs=false', '-c', 'core.fsmonitor=false',
            '-c', 'core.hooksPath=/dev/null',
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
        returncode = process.wait(timeout=max(0, deadline-time.monotonic()))
        if time.monotonic() >= deadline or returncode not in ((0, 1) if allow_exit_1 else (0,)):
            _stop('GIT_INSPECTION_FAILED')
        if errors:
            _stop('GIT_INSPECTION_FAILED')
        result = bytes(out)
        return (result, returncode) if return_exit_code else result
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


def _worktree_diff(root, source_sha):
    """Return a bounded source diff including safe untracked files."""
    tracked_patch = _git(root, ('diff', '--binary', '--no-ext-diff', '--no-textconv',
        '--no-renames', source_sha, '--'), limit=_GIT_LIMIT)
    tracked_raw = _git(root, ('diff', '--name-only', '-z', '--no-ext-diff',
        '--no-textconv', '--no-renames', source_sha, '--'), limit=1024 * 1024)
    untracked_raw = _git(root, ('ls-files', '--others', '--exclude-standard', '-z'),
                         limit=64 * 1024)
    try:
        tracked = set(path for path in tracked_raw.decode('utf-8', 'strict').split('\x00') if path)
        untracked = [path for path in untracked_raw.decode('utf-8', 'strict').split('\x00') if path]
    except UnicodeError:
        _stop('WORKTREE_PATH_INVALID')
    if len(untracked) > _PROPOSAL_FILE_LIMIT or len(set(untracked)) != len(untracked):
        _stop('WORKTREE_UNTRACKED_FILES')
    extra = []
    for path in untracked:
        parts = Path(path).parts
        if (not path or Path(path).is_absolute() or '\\' in path or
                any(part in ('', '.', '..') for part in parts) or
                any(ord(char) < 0x20 or ord(char) >= 0x7f for char in path)):
            _stop('WORKTREE_PATH_INVALID')
        target = Path(root).joinpath(*parts)
        try:
            before = target.lstat()
        except OSError:
            _stop('WORKTREE_FILE_UNAVAILABLE')
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size > _GIT_LIMIT):
            _stop('WORKTREE_FILE_UNSUPPORTED')
        raw = _git(root, ('diff', '--no-index', '--binary', '--no-ext-diff',
            '--no-textconv', '--no-renames', '/dev/null', path),
            limit=_GIT_LIMIT, allow_exit_1=True)
        try:
            after = target.lstat()
        except OSError:
            _stop('WORKTREE_FILE_CHANGED')
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            _stop('WORKTREE_FILE_CHANGED')
        # --no-index names /dev/null in its header. Emit Git's standard
        # new-file patch shape so the direct Developer parser can validate it.
        try:
            text = raw.decode('utf-8', 'strict')
        except UnicodeError:
            _stop('WORKTREE_PATH_INVALID')
        standard_header = f'diff --git a/{path} b/{path}\n'
        null_header = f'diff --git a/dev/null b/{path}\n'
        if text.startswith(null_header):
            text = standard_header + text[len(null_header):]
        elif not text.startswith(standard_header):
            _stop('WORKTREE_FILE_UNSUPPORTED')
        extra.append(text.encode('utf-8'))
    paths = tuple(sorted(tracked | set(untracked)))
    if len(paths) > _PROPOSAL_FILE_LIMIT:
        _stop('WORKTREE_SCOPE_MISMATCH')
    patch = tracked_patch + b''.join(extra)
    if len(patch) > _GIT_LIMIT:
        _stop('GIT_EVIDENCE_TOO_LARGE')
    return paths, patch


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
    _reject_legacy_grafts(root, common)
    branch = _git(root, ('branch', '--show-current'), limit=4096).decode().strip()
    head = _git(root, ('rev-parse', '--verify', 'HEAD'), limit=4096).decode().strip()
    status = _git(root, ('status', '--porcelain=v1', '-z', '--untracked-files=all'), limit=1024*1024)
    if (actual_root != root or not branch or _HEX40.fullmatch(head) is None or
            os.path.realpath(common) != common):
        _stop('GIT_BINDING_INVALID')
    _reject_legacy_grafts(root, common)
    return GitSnapshot(root, common, branch, head, status == b'')


def _reject_legacy_grafts(root, common_dir=None):
    """Refuse repositories whose legacy graft files can rewrite ancestry."""
    try:
        common_dir = common_dir or _git(
            root, ('rev-parse', '--path-format=absolute', '--git-common-dir'),
            limit=4096).decode('utf-8', 'strict').strip()
        git_dir = _git(root, ('rev-parse', '--path-format=absolute', '--git-dir'),
                       limit=4096).decode('utf-8', 'strict').strip()
        for info_dir in {Path(common_dir) / 'info', Path(git_dir) / 'info'}:
            grafts = info_dir / 'grafts'
            try:
                grafts.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                _stop('LEGACY_GRAFTS_UNSUPPORTED')
            # Any entry is unsupported: this includes symlinks, unreadable files,
            # directories, and other unexpected file types.
            _stop('LEGACY_GRAFTS_UNSUPPORTED')
    except LLMToolError:
        raise
    except Exception:
        _stop('LEGACY_GRAFTS_UNSUPPORTED')


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


def _developer_changed_paths(root, source_sha, result_sha, trusted_base_sha=None):
    """Return Developer-attributed paths, excluding trusted main history."""
    if trusted_base_sha is None:
        trusted_base_sha = source_sha
    if type(trusted_base_sha) is not str or _HEX40.fullmatch(trusted_base_sha) is None:
        _stop('GIT_INSPECTION_FAILED')

    commits = _git(root, ('rev-list', '--reverse', '--topo-order',
                          source_sha + '..' + result_sha), limit=1024 * 1024)
    try:
        commit_ids = [item for item in commits.decode('ascii').splitlines() if item]
    except UnicodeError:
        _stop('GIT_INSPECTION_FAILED')
    if len(commit_ids) > 4096:
        _stop('RESULT_DIFF_INVALID')

    listed_commits = set(commit_ids)
    commit_parents = {}
    for commit in commit_ids:
        raw = _git(root, ('rev-list', '--parents', '-n', '1', commit), limit=4096)
        fields = raw.decode('ascii').split()
        if len(fields) < 2:
            _stop('SOURCE_NOT_ANCESTOR')
        commit_parents[commit] = fields[1:]

    current = result_sha
    while current != source_sha:
        if current not in listed_commits:
            _stop('SOURCE_NOT_ANCESTOR')
        current = commit_parents[current][0]

    inherited = set()
    merge_parents = {}
    merge_resolution_paths = {}
    for commit in commit_ids:
        fields = [commit, *commit_parents[commit]]
        if len(fields) < 3:
            continue
        second_parent = fields[2]
        if not _is_ancestor(root, second_parent, trusted_base_sha):
            continue
        merge_parents[commit] = second_parent
        history = _git(root, ('rev-list', '--max-count=4097', second_parent,
                              '^' + source_sha), limit=1024 * 1024)
        inherited_commits = history.decode('ascii').splitlines()
        if len(inherited_commits) > 4096:
            _stop('RESULT_DIFF_INVALID')
        inherited.update(inherited_commits)
        if len(fields) > 3:
            continue
        merge_resolution_paths[commit] = _merge_resolution_paths(
            root, commit, fields[1], second_parent)

    developer_paths = set()
    for commit in commit_ids:
        if commit in inherited:
            continue
        fields = [commit, *commit_parents[commit]]
        if len(fields) > 3 and commit in merge_parents:
            _stop('RESULT_DIFF_INVALID')
        first_parent = fields[1]
        changed = _git(root, ('diff-tree', '--no-commit-id', '--name-only', '-r', '-z',
                              '--no-renames', first_parent, commit, '--'),
                       limit=1024 * 1024)
        try:
            paths = [item for item in changed.decode('utf-8', 'strict').split('\x00') if item]
        except UnicodeError:
            _stop('GIT_INSPECTION_FAILED')
        second_parent = merge_parents.get(commit)
        for path in paths:
            if (second_parent is not None and
                    path not in merge_resolution_paths.get(commit, set()) and
                    _tree_blob(root, commit, path) == _tree_blob(root, second_parent, path)):
                continue
            developer_paths.add(path)
            if len(developer_paths) > 128:
                _stop('RESULT_DIFF_INVALID')
        developer_paths.update(merge_resolution_paths.get(commit, set()))
        if len(developer_paths) > 128:
            _stop('RESULT_DIFF_INVALID')
    return tuple(sorted(developer_paths))


def _merge_resolution_paths(root, commit, first_parent, second_parent):
    """Return the paths a two-parent merge resolved by hand or conflicted on."""
    resolution_raw = _remerge_diff_paths_raw(root, commit)
    conflict_raw = _merge_tree_conflict_paths_raw(root, first_parent, second_parent)
    try:
        resolution_paths = set(
            path for path in resolution_raw.decode('utf-8', 'strict').split('\x00')
            if path)
        conflict_fields = conflict_raw.decode('utf-8', 'strict').split('\x00')
        if (len(conflict_fields) < 2 or
                re.fullmatch(r'[0-9a-f]{40}', conflict_fields[0]) is None or
                conflict_fields[-1] != ''):
            _stop('GIT_INSPECTION_FAILED')
        conflict_paths = conflict_fields[1:-1]
        if any(not path for path in conflict_paths):
            _stop('GIT_INSPECTION_FAILED')
    except UnicodeError:
        _stop('GIT_INSPECTION_FAILED')
    return resolution_paths | set(conflict_paths)


def _developer_tip(root, start_sha, head_sha, trusted_base_sha=None):
    """Return head_sha with its trailing clean trusted-main merges stripped.

    A stripped merge has exactly two parents, a trusted second parent and no
    resolution or conflict path, so it adds nothing to its first parent except
    trusted main. The first commit that fails is returned, or else start_sha.
    """
    if trusted_base_sha is None:
        trusted_base_sha = start_sha
    current = head_sha
    while current != start_sha:
        raw = _git(root, ('rev-list', '--parents', '-n', '1', current), limit=4096)
        parents = raw.decode('ascii').split()[1:]
        if (len(parents) != 2 or not _is_ancestor(root, parents[1], trusted_base_sha) or
                _merge_resolution_paths(root, current, parents[0], parents[1])):
            return current
        current = parents[0]
    return start_sha


def read_authenticated_main_sha():
    """Return the commit of main read from GitHub for the trusted repository.

    This is the only anchor for "trusted main": a PR base.sha, origin/main or any
    local ref can be stale or Developer-controlled. Every failure stops as
    MAIN_REF_PIN_INVALID.
    """
    try:
        response = _github_get('git/ref/heads/main')
    except Exception:
        _stop('MAIN_REF_PIN_INVALID')
    if (type(response) is not dict or response.get('ref') != 'refs/heads/main' or
            type(response.get('object')) is not dict or
            response['object'].get('type') != 'commit' or
            type(response['object'].get('sha')) is not str or
            _HEX40.fullmatch(response['object']['sha']) is None):
        _stop('MAIN_REF_PIN_INVALID')
    return response['object']['sha']


def _commit_present(root, sha):
    try:
        _git(root, ('cat-file', '-e', sha + '^{commit}'), limit=4096)
    except LLMToolError:
        return False
    return True


def ensure_main_commit(root, main_sha):
    """Return whether main_sha is local, fetching origin/main at most once."""
    if _commit_present(root, main_sha):
        return True
    source_freshness._fetch_origin_main(root)
    return _commit_present(root, main_sha)


def head_adds_only_main(root, recorded_head, head):
    """Return whether head is recorded_head plus only clean merges of fresh main.

    Main is read afresh for each decision. Anything unreadable is not admitted.
    """
    if any(type(sha) is not str or _HEX40.fullmatch(sha) is None
           for sha in (recorded_head, head)):
        return False
    try:
        main_sha = read_authenticated_main_sha()
        if not ensure_main_commit(root, main_sha):
            return False
        # A graft file could rewrite the parents that the walk below trusts.
        _reject_legacy_grafts(root)
        return _developer_tip(root, recorded_head, head, main_sha) == recorded_head
    except Exception:
        return False


def _diff_paths(root, start_sha, end_sha):
    """Return the sorted paths that differ between two commits."""
    changed = _git(root, ('diff', '--name-only', '-z', '--no-ext-diff', '--no-textconv',
                          '--no-renames', start_sha, end_sha, '--'), limit=1024 * 1024)
    return tuple(sorted(set(path for path in changed.decode('utf-8', 'strict').split('\x00')
                            if path)))


def _private_merge_read(root, args, **git_options):
    """Run one merge read in a private temporary bare repository.

    A merge in the Developer repository would honour its merge.* config,
    custom merge drivers, info/attributes and .gitattributes: a driver could
    run a command here and hide a conflict path. The private repository has
    only core.bare=true config, an unborn HEAD (so no attribute tree), no
    index and no attributes. Repository objects are reachable only as
    read-only alternates, and every object the merge writes stays private.
    Every failure stops as GIT_INSPECTION_FAILED, including the timeout,
    unavailable and oversized-output codes that _git classifies.
    """
    try:
        objects = _git(root, ('rev-parse', '--path-format=absolute', '--git-path', 'objects'),
                       limit=4096).decode('utf-8', 'strict').strip()
        if not objects or not os.path.isabs(objects):
            _stop('GIT_INSPECTION_FAILED')
        with tempfile.TemporaryDirectory(prefix='llmtool-merge-read-') as temporary:
            private = Path(temporary).resolve()
            (private / 'objects').mkdir(mode=0o700)
            (private / 'refs').mkdir(mode=0o700)
            (private / 'HEAD').write_text('ref: refs/heads/llmtool-merge-read\n',
                                          encoding='ascii')
            (private / 'config').write_text(
                '[core]\n\trepositoryformatversion = 0\n\tbare = true\n',
                encoding='ascii')
            return _git(str(private), args, env_overrides={
                'GIT_DIR': str(private),
                'GIT_ALTERNATE_OBJECT_DIRECTORIES': objects,
            }, **git_options)
    except Exception:
        _stop('GIT_INSPECTION_FAILED')


def _remerge_diff_paths_raw(root, commit):
    """Read resolution paths without repository merge config or attributes."""
    return _private_merge_read(root, ('show', '--remerge-diff', '--format=', '--no-renames',
                                      '--name-only', '-z', commit), limit=1024 * 1024)


def _merge_tree_conflict_paths_raw(root, first_parent, second_parent):
    """List conflict paths without repository merge config or attributes."""
    return _private_merge_read(root, ('merge-tree', '--write-tree', '--name-only',
                                      '--no-messages', '-z', first_parent, second_parent),
                               limit=1024 * 1024, allow_exit_1=True)


def _is_ancestor(root, ancestor, descendant):
    result = subprocess.run(['/usr/bin/git', '-c', 'core.useReplaceRefs=false',
        '-C', root, 'merge-base', '--is-ancestor', ancestor, descendant],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=_TIMEOUT,
        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
             'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0',
             'GIT_NO_LAZY_FETCH': '1', 'GIT_NO_REPLACE_OBJECTS': '1'},
        shell=False, check=False)
    if result.returncode not in (0, 1):
        _stop('GIT_INSPECTION_FAILED')
    return result.returncode == 0


def _collect_registered_result_diff(*, task_id, expected_common_dir, expected_root,
                                     expected_branch, source_sha, task_binding,
                                     expected_agent, expected_session_ref,
                                     expected_native_session_id, write_paths,
                                     artifact_segments=None, trusted_base_sha=None):
    """Share D6, ENV2, HEAD, ancestry, scope and diff-hash checks across routes."""
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
    _reject_legacy_grafts(registration.root, registration.common_dir)
    snapshot = collect_git_snapshot(registration.root)
    if (snapshot.common_dir != registration.common_dir or snapshot.branch != registration.branch or
            snapshot.head != registration.result_sha or not snapshot.clean):
        _stop('RESULT_HEAD_MISMATCH')
    validate_env2_pass_marker(registration, expected_root=expected_root,
                              current_head=snapshot.head)
    try:
        _git(registration.root, ('merge-base', '--is-ancestor', registration.source_sha, source_sha),
             limit=4096)
    except LLMToolError:
        _stop('D6_TASK_BINDING_MISMATCH')
    if source_sha == snapshot.head:
        _stop('RESULT_DIFF_EMPTY')
    _, ancestor_status = _git(registration.root,
        ('merge-base', '--is-ancestor', source_sha, snapshot.head),
        limit=4096, allow_exit_1=True, return_exit_code=True)
    if ancestor_status != 0:
        _stop('SOURCE_NOT_ANCESTOR')
    # The 128-path limit counts Developer paths only: a merged main can bring in
    # any number of its own files.
    paths = _diff_paths(registration.root, source_sha, snapshot.head)
    if not paths:
        _stop('RESULT_DIFF_INVALID')
    developer_paths = _developer_changed_paths(
        registration.root, source_sha, snapshot.head, trusted_base_sha)
    if not developer_paths or len(developer_paths) > 128:
        _stop('RESULT_DIFF_INVALID')
    if not set(developer_paths).issubset(set(write_paths)):
        _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID' if artifact_segments is not None
              else 'RESULT_SCOPE_MISMATCH')
    patch = _git(registration.root, ('diff', '--binary', '--no-ext-diff', '--no-textconv',
                '--no-renames', source_sha, snapshot.head, '--'), limit=_GIT_LIMIT)
    return registration, snapshot, paths, developer_paths, patch


def collect_executor_result(*, task_id, expected_common_dir, expected_root,
                           expected_branch, source_sha, task_binding,
                           expected_agent, expected_session_ref,
                           expected_native_session_id, write_paths, artifact, artifact_sha256,
                           artifact_segments=None, trusted_base_sha=None):
    """Compare the current registered Executor HEAD and actual source-to-result diff."""
    registration, snapshot, paths, developer_paths, patch = _collect_registered_result_diff(
        task_id=task_id, expected_common_dir=expected_common_dir,
        expected_root=expected_root, expected_branch=expected_branch,
        source_sha=source_sha, task_binding=task_binding,
        expected_agent=expected_agent, expected_session_ref=expected_session_ref,
        expected_native_session_id=expected_native_session_id,
        write_paths=write_paths, artifact_segments=artifact_segments,
        trusted_base_sha=trusted_base_sha)
    # An artifact describes the Developer's work, so it is compared with the
    # Developer tip. Paths, digest and scope above still cover the full head.
    if artifact_segments is None:
        tip = _developer_tip(registration.root, source_sha, snapshot.head, trusted_base_sha)
        verify_artifact_applied(registration.root, artifact, artifact_sha256,
                                source_sha, tip,
                                _diff_paths(registration.root, source_sha, tip),
                                write_paths, scope_paths=developer_paths)
    else:
        if (type(artifact_segments) is not list or not artifact_segments or
                any(type(segment) is not tuple or len(segment) != 4
                    for segment in artifact_segments)):
            _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
        if (artifact_segments[0][2] != source_sha or
                artifact_segments[-1][3] != snapshot.head):
            _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
        previous_end = source_sha
        for segment in artifact_segments:
            segment_artifact, segment_digest, start_sha, end_sha = segment
            if (start_sha != previous_end or
                    type(start_sha) is not str or _HEX40.fullmatch(start_sha) is None or
                    type(end_sha) is not str or _HEX40.fullmatch(end_sha) is None):
                _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
            try:
                _git(registration.root, ('merge-base', '--is-ancestor', start_sha, end_sha),
                     limit=4096)
            except LLMToolError:
                _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
            segment_paths = _diff_paths(registration.root, start_sha, end_sha)
            segment_developer_paths = _developer_changed_paths(
                registration.root, start_sha, end_sha, trusted_base_sha)
            if (not segment_paths or not segment_developer_paths or
                    not set(segment_developer_paths).issubset(set(write_paths))):
                _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
            segment_tip = _developer_tip(registration.root, start_sha, end_sha,
                                         trusted_base_sha)
            try:
                verify_artifact_applied(registration.root, segment_artifact,
                    segment_digest, start_sha, segment_tip,
                    _diff_paths(registration.root, start_sha, segment_tip), write_paths,
                    scope_paths=segment_developer_paths)
            except LLMToolError:
                _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
            previous_end = end_sha
    _reject_legacy_grafts(registration.root, registration.common_dir)
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
        'developer_changed_paths': list(developer_paths),
        'diff_sha256': hashlib.sha256(patch).hexdigest(),
    }


def collect_claude_result(*, task_id, expected_common_dir, expected_root,
                          expected_branch, source_sha, task_binding,
                          expected_agent, expected_session_ref,
                          expected_native_session_id, write_paths, claude_result,
                          trusted_base_sha=None):
    """Verify a bound Claude result using D6 evidence, without an artifact claim."""
    registration, snapshot, paths, developer_paths, patch = _collect_registered_result_diff(
        task_id=task_id, expected_common_dir=expected_common_dir,
        expected_root=expected_root, expected_branch=expected_branch,
        source_sha=source_sha, task_binding=task_binding,
        expected_agent=expected_agent, expected_session_ref=expected_session_ref,
        expected_native_session_id=expected_native_session_id,
        write_paths=write_paths, trusted_base_sha=trusted_base_sha)
    if (type(claude_result) is not dict or
            claude_result.get('executor_task_id') != registration.task_id):
        _stop('CLAUDE_RESULT_BINDING_INVALID')
    claimed_head = claude_result.get('head_sha')
    if claimed_head != registration.result_sha:
        # The bound result stays valid when the head only adds clean main merges.
        if (type(claimed_head) is not str or _HEX40.fullmatch(claimed_head) is None or
                _developer_tip(registration.root, claimed_head, snapshot.head,
                               trusted_base_sha) != claimed_head):
            _stop('CLAUDE_RESULT_BINDING_INVALID')
    elif claimed_head != snapshot.head:
        _stop('RESULT_HEAD_MISMATCH')
    _reject_legacy_grafts(registration.root, registration.common_dir)
    return {
        'task_id': task_binding.get('task_id'), 'run_id': task_binding.get('run_id'),
        'executor_task_id': registration.task_id,
        'executor_identity': registration.trusted_agent_id,
        'executor_session_ref': registration.session_ref,
        'executor_native_session_id': registration.native_session_id,
        'executor_native_session': ('VERIFIED' if registration.native_session_id else 'UNKNOWN'),
        'root': registration.root, 'common_dir': registration.common_dir,
        'branch': registration.branch, 'source_sha': source_sha,
        'result_head': snapshot.head, 'changed_paths': list(paths),
        'developer_changed_paths': list(developer_paths),
        'diff_sha256': hashlib.sha256(patch).hexdigest(),
    }


def _worktree_snapshot(*, root, source_sha, read_paths, write_paths, expected_head=None):
    """Hash a registered uncommitted result without promoting it to a commit."""
    snapshot = collect_git_snapshot(root)
    if snapshot.head != (source_sha if expected_head is None else expected_head):
        _stop('WORKTREE_SOURCE_HEAD_MISMATCH')
    status = _git(root, ('status', '--porcelain=v1', '-z', '--untracked-files=all'),
                   limit=1024 * 1024)
    untracked_raw = _git(root, ('ls-files', '--others', '--exclude-standard', '-z'),
                         limit=64 * 1024)
    try:
        untracked = [path for path in untracked_raw.decode('utf-8', 'strict').split('\x00')
                     if path]
    except UnicodeError:
        _stop('WORKTREE_PATH_INVALID')
    if any(path not in set(write_paths) for path in untracked):
        _stop('WORKTREE_UNTRACKED_FILES')
    changed, patch = _worktree_diff(root, source_sha)
    if not changed and not patch:
        _stop('WORKTREE_SCOPE_MISMATCH')
    for path in changed:
        parts = Path(path).parts
        if (Path(path).is_absolute() or not parts or
                any(part in ('', '.', '..') for part in parts) or '\\' in path or
                any(ord(char) < 0x20 or ord(char) >= 0x7f for char in path)):
            _stop('WORKTREE_PATH_INVALID')
    if any(path not in set(write_paths) for path in changed):
        _stop('WORKTREE_SCOPE_MISMATCH')

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
                                     expected_result_head=None,
                                     expected_agent, expected_session_ref,
                                     expected_native_session_id, write_paths, read_paths,
                                     artifact, artifact_sha256):
    """Bind a real D6 uncommitted diff and refuse to call it a result commit."""
    registration = read_d6_registration(task_id, expected_common_dir,
                                        allowed_states=_D6_EVIDENCE_STATES)
    result_head = source_sha if expected_result_head is None else expected_result_head
    if (type(result_head) is not str or _HEX40.fullmatch(result_head) is None or
            registration.root != expected_root or registration.branch != expected_branch or
            registration.result_sha != result_head or
            registration.trusted_agent_id != expected_agent or
            registration.session_ref != expected_session_ref or
            registration.native_session_id != expected_native_session_id or
            type(source_sha) is not str or _HEX40.fullmatch(source_sha) is None):
        _stop('D6_TASK_BINDING_MISMATCH')
    if (type(task_binding) is not dict or
            any(type(task_binding.get(key)) is not str for key in
                ('task_id', 'run_id', 'contract_digest'))):
        _stop('TASK_BINDING_MISSING')
    _reject_legacy_grafts(registration.root, registration.common_dir)
    untracked_raw = _git(registration.root,
        ('ls-files', '--others', '--exclude-standard', '-z'), limit=64 * 1024)
    try:
        untracked = [path for path in untracked_raw.decode('utf-8', 'strict').split('\x00')
                     if path]
    except UnicodeError:
        _stop('WORKTREE_PATH_INVALID')
    if any(path not in set(write_paths) for path in untracked):
        _stop('WORKTREE_UNTRACKED_FILES')
    live = _worktree_snapshot(root=registration.root, source_sha=source_sha,
                              read_paths=read_paths, write_paths=write_paths,
                              expected_head=result_head)
    if (live['common_dir'] != registration.common_dir or
            live['branch'] != registration.branch or live['source_sha'] != result_head):
        _stop('D6_TASK_BINDING_MISMATCH')
    validate_env2_pass_marker(registration, expected_root=expected_root,
                              current_head=live['source_sha'])
    try:
        _git(registration.root, ('merge-base', '--is-ancestor',
                                 registration.source_sha, source_sha), limit=4096)
        _git(registration.root, ('merge-base', '--is-ancestor', source_sha,
                                 result_head), limit=4096)
    except LLMToolError:
        _stop('D6_TASK_BINDING_MISMATCH')
    _reject_legacy_grafts(registration.root, registration.common_dir)
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
                            changed_paths, write_paths, *, scope_paths=None):
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
    scope_paths = patch_paths if scope_paths is None else scope_paths
    if (not patch_paths or len(patch_paths) > _PROPOSAL_FILE_LIMIT or
            len(patch_paths) != len(set(patch_paths)) or
            set(patch_paths) != set(changed_paths) or
            not set(scope_paths).issubset(set(write_paths))):
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
           'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_LAZY_FETCH': '1',
           'GIT_NO_REPLACE_OBJECTS': '1'}
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
                ['/usr/bin/git', '-c', 'core.useReplaceRefs=false',
                 '-c', 'core.hooksPath=/dev/null', 'apply',
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
        if (path.endswith('.md') or path in ('AGENTS.md', 'CLAUDE.md') or
                path.startswith(('skills/', 'docs/'))):
            continue
        return False
    return True


# The trusted workflow whose code check a scripts/-only change does not run.
_SCRIPTS_ONLY_SKIPPED_CODE_CHECK = 'check (Linux 3.14)'


def _scripts_only_ci(paths):
    """Return whether every changed path is under scripts/ (not docs-only).

    Same rule as task_advance._scripts_only_ci: such a change runs no Linux job.
    """
    return (type(paths) is list and bool(paths) and
            all(type(path) is str and path.startswith('scripts/') for path in paths) and
            not _docs_only_ci(paths))


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


def _test_excerpt(home, output, code):
    """Return only bounded excerpts accepted by the sensitive-output screener."""
    if not code:
        return None
    # macOS may expose temporary directories through a /var symlink. Resolve
    # the trusted TemporaryDirectory path before the excerpt reader's strict
    # no-follow walk, which intentionally rejects symlinked components.
    capture = Path(home).resolve(strict=True) / 'test-output.log'
    capture.write_bytes(output)
    try:
        selected = select_test_excerpts(str(capture), code)
    except LLMToolError:
        return None
    return selected['excerpts']


def _run_registered_tests(root, source_sha, eligible, *, home, pycache=None):
    evidence = []
    for path, kind in eligible:
        env = {'PATH': os.path.dirname(sys.executable) + ':/usr/bin:/bin',
               'LC_ALL': 'C', 'HOME': home,
               'PYTHONPATH': str(Path(root) / 'tools' / 'llmtool'),
               'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': home}
        if pycache is not None:
            env['PYTHONPYCACHEPREFIX'] = pycache
        if kind == 'unittest':
            argv = [sys.executable, '-B', '-m', 'unittest', 'discover',
                    '-s', 'tools/llmtool/tests', '-p', Path(path).name]
        elif kind == 'script':
            argv = [sys.executable, '-I', '-B', '-m', 'unittest', 'discover',
                    '-s', 'scripts', '-p', Path(path).name]
        else:
            argv = [sys.executable, '-I', '-B', path]
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
        item = {
            'path': path, 'source_sha': source_sha, 'exit_code': code,
            'output_sha256': hashlib.sha256(output).hexdigest(),
            'duration_ms': max(0, int((time.monotonic()-started)*1000)),
            'command_sha256': hashlib.sha256(json.dumps(argv, separators=(',', ':')).encode()).hexdigest(),
        }
        ran = re.findall(rb'(?m)^Ran ([0-9]+) tests?\b', output) if kind == 'script' else None
        if code or (kind == 'script' and (not ran or int(ran[-1]) == 0)):
            item['output_excerpt'] = _test_excerpt(home, output, code)
            item['status'] = 'FAILED'
        else:
            item['status'] = 'PASSED'
        evidence.append(item)
    return evidence


def run_bounded_tests(*, root, source_sha, result_sha, read_paths, write_paths,
                      trusted_base_sha=None):
    """Run fixed registered tests or record an actual Git-derived docs-only result.

    With trusted_base_sha the docs-only decision uses only Developer-attributed
    paths: files that a clean merge of trusted main brought in are not Task
    changes. A merge whose second parent is not in trusted main, or that has a
    conflict or hand resolution, still counts its paths. Without it the plain
    source_sha..result_sha diff decides, as before.
    """
    eligible = sorted({(p, _registered_test(p)) for p in (*read_paths, *write_paths)
                       if _registered_test(p) is not None})
    before = collect_git_snapshot(root)
    if before.head != result_sha or not before.clean:
        _stop('TEST_SOURCE_MISMATCH')
    if trusted_base_sha is None:
        changed_paths = _committed_changed_paths(root, source_sha, result_sha)
    else:
        changed_paths = list(_developer_changed_paths(
            root, source_sha, result_sha, trusted_base_sha))
        if not changed_paths or len(changed_paths) > _PROPOSAL_FILE_LIMIT:
            _stop('TEST_PATH_INVALID')
    if _docs_only(changed_paths):
        return [{'status': 'NOT_APPLICABLE_DOCS_ONLY', 'changed_paths': changed_paths,
                 'source_sha': result_sha, 'docs_only': True}]
    if not eligible:
        _stop('TEST_SCOPE_MISSING')
    evidence = []
    with tempfile.TemporaryDirectory(prefix='llmtool-task-tests-') as home:
        evidence = _run_registered_tests(root, result_sha, eligible, home=home)
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
    before = _worktree_snapshot(root=root, source_sha=source_sha,
                                read_paths=read_paths, write_paths=write_paths)
    if (before['diff_sha256'] != diff_sha256 or
            before['changed_paths'] != changed_paths):
        _stop('TEST_SOURCE_MISMATCH')
    if _docs_only(before['changed_paths']):
        return [{'status': 'NOT_APPLICABLE_DOCS_ONLY',
                 'changed_paths': before['changed_paths'], 'source_sha': source_sha,
                 'diff_sha256': diff_sha256, 'result_head': None, 'docs_only': True}]
    eligible = sorted({(p, _registered_test(p)) for p in (*read_paths, *write_paths)
                       if _registered_test(p) is not None})
    if not eligible:
        _stop('TEST_SCOPE_MISSING')
    with tempfile.TemporaryDirectory(prefix='llmtool-task-worktree-tests-') as home, \
            tempfile.TemporaryDirectory(prefix='llmtool-task-python-cache-') as pycache:
        evidence = _run_registered_tests(root, source_sha, eligible,
                                         home=home, pycache=pycache)
        for item in evidence:
            item.update({'result_head': None, 'diff_sha256': diff_sha256})
    after = _worktree_snapshot(root=root, source_sha=source_sha,
                               read_paths=read_paths, write_paths=write_paths)
    if after != before:
        _stop('TEST_SOURCE_CHANGED')
    return evidence


def _trust_config():
    """Return the frozen TrustConfig, read at use time; fail closed with TRUST_*."""
    try:
        return trust.load_trust()
    except trust.TrustError as exc:
        code = exc.code
    _stop(code)


def _trusted_slug():
    return _trust_config().repository['slug']


def _ci_trust():
    return _trust_config().ci_trust


def _github_get(path):
    # The repository is private, so an anonymous request always gets 404. Read
    # through the signed-in GitHub CLI: llmtool never sees its credential, and
    # ambient token/proxy variables from this process are not forwarded.
    if type(path) is not str or not _GITHUB_API_PATH.fullmatch(path):
        _stop('GITHUB_EVIDENCE_UNAVAILABLE')
    gh = shutil.which('gh', path=_GH_PATH)
    if gh is None:
        _stop('GITHUB_EVIDENCE_UNAVAILABLE')
    slug = _trusted_slug()
    env = {'PATH': _GH_PATH, 'HOME': os.path.expanduser('~'), 'LC_ALL': 'C',
           'GH_PROMPT_DISABLED': '1', 'GH_NO_UPDATE_NOTIFIER': '1', 'NO_COLOR': '1'}
    if os.environ.get('GH_CONFIG_DIR'):
        env['GH_CONFIG_DIR'] = os.environ['GH_CONFIG_DIR']
    argv = [gh, 'api', '--method', 'GET',
            '-H', 'Accept: application/vnd.github+json',
            '-H', 'X-GitHub-Api-Version: 2022-11-28',
            'repos/' + slug + '/' + path]
    try:
        process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
            close_fds=True, start_new_session=True)
    except OSError:
        _stop('GITHUB_EVIDENCE_UNAVAILABLE')
    raw = bytearray()
    deadline = time.monotonic() + 8
    selector = selectors.DefaultSelector()
    try:
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop('GITHUB_EVIDENCE_UNAVAILABLE')
            for key, _ in selector.select(min(remaining, .1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif len(raw) + len(chunk) > 1024 * 1024:
                    _stop('GITHUB_EVIDENCE_TOO_LARGE')
                else:
                    raw.extend(chunk)
        code = process.wait(timeout=max(0, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired):
        _stop('GITHUB_EVIDENCE_UNAVAILABLE')
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, 9)
            except OSError:
                process.kill()
            process.wait()
        process.stdout.close()
    if code != 0:
        _stop('GITHUB_EVIDENCE_UNAVAILABLE')
    raw = bytes(raw)
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                          parse_constant=lambda _: _stop('GITHUB_EVIDENCE_INVALID'))
    except Exception:
        _stop('GITHUB_EVIDENCE_INVALID')


def _review_time(review, field='submitted_at'):
    submitted = review.get(field)
    if type(submitted) is not str:
        _stop('REVIEW_EVIDENCE_INVALID')
    try:
        parsed = datetime.fromisoformat(submitted.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            _stop('REVIEW_EVIDENCE_INVALID')
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        _stop('REVIEW_EVIDENCE_INVALID')


def _recorded_model_family(model):
    """Return 'claude', 'codex' or None for a recorded run model name.

    The name is normalised by dropping any provider prefix up to the last
    '/', dropping any variant suffix from the first '#', and lowercasing.
    """
    if type(model) is not str:
        return None
    name = model.rsplit('/', 1)[-1].split('#', 1)[0].lower()
    if name.startswith('claude'):
        return 'claude'
    if name.startswith(('gpt', 'codex')) or re.match(r'o[0-9]', name):
        return 'codex'
    return None


def _reviewer_author_family(developer):
    """Return the known family relevant to formal Reviewer separation.

    The route profile and the recorded run model both count: a multi-model
    route takes the family of its recorded model, and a single-family route
    whose recorded model belongs to the other Reviewer family fails closed.
    """
    if type(developer) is not dict:
        return None
    profile = developer.get('provider_profile')
    if profile == 'claude':
        if _recorded_model_family(developer.get('model')) == 'codex':
            return None
        return 'claude'
    elif profile == 'codex':
        if _recorded_model_family(developer.get('model')) == 'claude':
            return None
        return 'codex'
    elif profile == 'agy':
        model = developer.get('model')
        if type(model) is str and model.startswith('claude-'):
            return 'claude'
        if type(model) is str and model.startswith('gemini-'):
            return 'other'
        return None
    elif profile in ('opencode', 'company-gw', 'company-gw-agent', 'gw-agent'):
        return _recorded_model_family(developer.get('model')) or 'other'
    else:
        return None


def _review_family_compatible(developer, reviewer_identity):
    """Require a known Developer family and the matching formal Reviewer family."""
    developer_family = _reviewer_author_family(developer)
    reviewer_family = {
        'claude-reviewer': 'claude',
        'codex-reviewer': 'codex',
    }.get(reviewer_identity)
    if developer_family is None or reviewer_family is None:
        return False
    return reviewer_family != developer_family


def _company_gw_text_proposal_pm_session(task_record, executor_identity):
    """Return the PM session of a PM-applied company-gw text-proposal Task, else None.

    Only an exact company-gw Developer record qualifies: no exposed native
    session, a responded unified diff with a sha256, applied in the D6
    registered to the company-gw trusted agent, and a known PM session.
    """
    if type(task_record) is not dict or executor_identity != 'company-gw':
        return None
    developer = task_record.get('developer')
    pm_session = task_record.get('pm_session_id')
    if (type(developer) is not dict or
            developer.get('provider_profile') != 'company-gw' or
            developer.get('backend_session_semantics') != 'NO_NATIVE_SESSION_ID_EXPOSED' or
            developer.get('state') != 'DEVELOPER_RESPONDED' or
            developer.get('artifact_format') != 'unified_diff' or
            type(developer.get('artifact_sha256')) is not str or
            _HEX64.fullmatch(developer['artifact_sha256']) is None or
            _native_session_identity(pm_session) is None):
        return None
    return pm_session


def _native_session_identity(value):
    """Return a comparable native session identity, else None.

    A Task stores a UUID-backed PM session as ``pm-<native UUID>`` while D6
    records hold the raw native UUID, so one ``pm-`` prefix is removed and the
    rest is case-folded. Empty or UNKNOWN identities give None.
    """
    if type(value) is not str:
        return None
    identity = value.strip()
    if identity[:3].casefold() == 'pm-':
        identity = identity[3:].strip()
    identity = identity.casefold()
    if not identity or identity == 'unknown':
        return None
    return identity


def _review_binding_line(task_id, run_id, contract_digest, result_sha):
    return (f'LLMTOOL-REVIEW-BINDING v1 task={task_id} run={run_id} '
            f'contract={contract_digest} head={result_sha}')


def is_formal_claude_pass(body, *, task_id, run_id, contract_digest, result_sha):
    """Return whether a comment is the exact task-bound formal Claude PASS."""
    if type(body) is not str:
        return False
    raw_lines = [line for line in body.splitlines() if line.strip()]
    if len(raw_lines) < 2:
        return False
    marker = _VERDICT_MARKER.fullmatch(raw_lines[0])
    status = _VERDICT_STATUS.fullmatch(raw_lines[1])
    binding_line = _review_binding_line(task_id, run_id, contract_digest, result_sha)
    lines = [line.strip() for line in raw_lines]
    return (marker is not None and status is not None and
            marker.group(1) == 'Claude' and marker.group(2) == 'PASS' and
            status.group(1) == 'PASS' and status.group(2) == result_sha and
            _FORMAL_ACCEPT in lines and binding_line in lines)


def _labels_equal(labels, expected):
    return (type(labels) is list and all(type(label) is str for label in labels) and
            set(labels) == set(expected))


def _hosted_label_alternatives(runner_labels):
    """github-hosted runner_labels as alternatives: a flat label list is one
    alternative; a nested form lists them (#1253). Any other shape trusts none."""
    if all(type(item) is str for item in runner_labels):
        return (runner_labels,)
    if all(type(item) in (list, tuple) and all(type(label) is str for label in item)
           for item in runner_labels):
        return tuple(runner_labels)
    return ()


def _trusted_ci_runner(job, *, mode, runner_names, runner_labels):
    """Exact runner identity from ci_trust; never prefix, substring or wildcard."""
    labels = job.get('labels')
    if mode == 'self-hosted':
        runner_name = job.get('runner_name')
        return (type(runner_name) is str and runner_names is not None and
                runner_name in runner_names and
                (runner_labels is None or _labels_equal(labels, runner_labels)))
    if mode == 'github-hosted':
        return (runner_labels is not None and
                any(_labels_equal(labels, alternative)
                    for alternative in _hosted_label_alternatives(runner_labels)) and
                'self-hosted' not in labels and
                job.get('runner_group_name') == _GITHUB_HOSTED_RUNNER_GROUP)
    return False


def _collect_one_ci_workflow(*, pr_number, result_sha, workflow_path,
                             workflow_name, check_name, runner_names,
                             runner_labels=None, mode='self-hosted',
                             app_id=_GITHUB_ACTIONS_APP_ID,
                             pr_merged=False, pr_head_ref=None):
    if type(app_id) is not int or app_id != _GITHUB_ACTIONS_APP_ID:
        _stop('TRUST_CI_INVALID')
    slug = _trusted_slug()
    workflow = _github_get('actions/workflows/' + workflow_path.rsplit('/', 1)[-1])
    if (type(workflow) is not dict or type(workflow.get('id')) is not int or
            workflow['id'] <= 0 or workflow.get('path') != workflow_path or
            workflow.get('name') != workflow_name or workflow.get('state') != 'active'):
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
        matching_pr = (type(pull_requests) is list and
                       any(type(item) is dict and item.get('number') == pr_number and
                           type(item.get('head')) is dict and
                           item['head'].get('sha') == result_sha for item in pull_requests))
        merged_empty_prs_match = (
            pr_merged is True and type(pull_requests) is list and not pull_requests and
            run.get('head_branch') == pr_head_ref and
            type(run.get('head_repository')) is dict and
            run['head_repository'].get('full_name') == slug)
        if (run.get('workflow_id') != workflow_id or run.get('path') != workflow_path or
                run.get('event') != 'pull_request' or run.get('head_sha') != result_sha or
                not (matching_pr or merged_empty_prs_match)):
            _stop('CI_WORKFLOW_RUN_MISMATCH')
        if (type(run.get('id')) is not int or run['id'] <= 0 or
                type(run.get('run_number')) is not int or run['run_number'] <= 0 or
                type(run.get('run_attempt')) is not int or run['run_attempt'] <= 0):
            _stop('CI_WORKFLOW_RUNS_INVALID')
        candidates.append(run)
    if not candidates:
        return {'workflow_id': workflow_id, 'run_id': None, 'run_attempt': None,
                'run_number': None, 'successful_checks': [], 'check_ids': {},
                'check_outcomes': {check_name: 'MISSING'}}
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
        if job.get('name') != check_name:
            continue
        check_url = job.get('check_run_url')
        check_id_match = (None if type(check_url) is not str else re.fullmatch(
            r'https://api\.github\.com/repos/' + re.escape(slug) + r'/'
            r'check-runs/([1-9][0-9]{0,18})', check_url))
        if (check_name in required_jobs or job.get('run_id') != run_id or
                not _trusted_ci_runner(job, mode=mode, runner_names=runner_names,
                                       runner_labels=runner_labels) or
                type(job.get('id')) is not int or job['id'] <= 0 or
                check_id_match is None):
            _stop('CI_WORKFLOW_JOBS_INVALID')
        required_jobs[check_name] = (job, int(check_id_match.group(1)))
    if set(required_jobs) != {check_name}:
        return {'workflow_id': workflow_id, 'run_id': run_id, 'run_attempt': attempt,
                'run_number': run['run_number'], 'successful_checks': [], 'check_ids': {},
                'check_outcomes': {check_name: 'MISSING'}}
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
    successful, check_ids, check_outcomes = set(), {}, {}
    job, check_id = required_jobs[check_name]
    check = by_id.get(check_id)
    app = None if check is None else check.get('app')
    if (check is not None and check.get('name') == check_name and
            check.get('head_sha') == result_sha and type(app) is dict and
            app.get('id') == app_id and app.get('slug') == 'github-actions' and
            app.get('name') == 'GitHub Actions' and
            job.get('status') == 'completed' and job.get('conclusion') == 'success' and
            check.get('status') == 'completed' and check.get('conclusion') == 'success' and
            run.get('status') == 'completed' and run.get('conclusion') == 'success'):
        successful.add(check_name)
        check_ids[check_name] = check['id']
        check_outcomes[check_name] = 'SUCCESS'
    elif (check is None or check.get('name') != check_name or
          check.get('head_sha') != result_sha or type(app) is not dict or
          app.get('id') != app_id or app.get('slug') != 'github-actions' or
          app.get('name') != 'GitHub Actions'):
        check_outcomes[check_name] = 'MISSING'
    elif (run.get('status') != 'completed' or job.get('status') != 'completed' or
          check.get('status') != 'completed'):
        check_outcomes[check_name] = 'PENDING'
    elif (run.get('conclusion') in ('success', 'failure', 'cancelled', 'timed_out',
                                     'action_required', 'startup_failure', 'stale',
                                     'neutral') and
          job.get('conclusion') in ('success', 'failure', 'cancelled', 'timed_out',
                                     'action_required', 'startup_failure', 'stale',
                                     'neutral') and
          check.get('conclusion') in ('success', 'failure', 'cancelled', 'timed_out',
                                       'action_required', 'startup_failure', 'stale',
                                       'neutral')):
        check_outcomes[check_name] = 'FAILED'
    else:
        check_outcomes[check_name] = 'MISSING'
    return {'workflow_id': workflow_id, 'run_id': run_id, 'run_attempt': attempt,
            'run_number': run['run_number'], 'successful_checks': sorted(successful),
            'check_ids': check_ids, 'check_outcomes': check_outcomes}


def _collect_ci_evidence(*, pr_number, result_sha, changed_paths=None,
                         pr_merged=False, pr_head_ref=None):
    """Exact-head CI evidence for every trusted workflow on the selected path.

    Code path: every ci_trust workflow with a ``code_check`` must succeed and
    the first such workflow is primary; the docs-only path does the same with
    ``docs_check``. With the Owner's values this is the Mac + Linux code gate
    and the Linux ``check (docs)`` docs gate. A scripts/-only change runs no
    Linux job (as in task advance), so its code path leaves out the workflow
    whose code check is ``check (Linux 3.14)`` and requires the Mac check only.
    """
    ci_trust = _ci_trust()
    docs_only = _docs_only_ci(changed_paths)
    scripts_only = _scripts_only_ci(changed_paths)
    linux_required = not scripts_only
    field = 'docs_check' if docs_only else 'code_check'
    selected = [workflow for workflow in ci_trust['workflows']
                if workflow[field] is not None and
                not (scripts_only and field == 'code_check' and
                     workflow['code_check'] == _SCRIPTS_ONLY_SKIPPED_CODE_CHECK)]
    if not selected:
        _stop('TRUST_CI_INVALID')
    results = []
    for index, workflow in enumerate(selected):
        try:
            result = _collect_one_ci_workflow(
                pr_number=pr_number, result_sha=result_sha,
                workflow_path=workflow['path'], workflow_name=workflow['name'],
                check_name=workflow[field], runner_names=workflow['runner_names'],
                runner_labels=workflow['runner_labels'], mode=ci_trust['mode'],
                app_id=ci_trust['github_actions_app_id'], pr_merged=pr_merged,
                pr_head_ref=pr_head_ref)
        except LLMToolError as exc:
            # A malformed Mac job can otherwise mask a completed Linux failure.
            # Read the later required workflow only to surface that failure; if
            # it is not confirmed, preserve the original fail-closed error.
            if (index != 0 or getattr(exc, 'completion_code', None) !=
                    'CI_WORKFLOW_JOBS_INVALID' or
                    len(selected) < 2):
                raise
            later = []
            for later_workflow in selected[index + 1:]:
                try:
                    result = _collect_one_ci_workflow(
                        pr_number=pr_number, result_sha=result_sha,
                        workflow_path=later_workflow['path'],
                        workflow_name=later_workflow['name'],
                        check_name=later_workflow[field],
                        runner_names=later_workflow['runner_names'],
                        runner_labels=later_workflow['runner_labels'],
                        mode=ci_trust['mode'], app_id=ci_trust['github_actions_app_id'],
                        pr_merged=pr_merged, pr_head_ref=pr_head_ref)
                except LLMToolError:
                    # A secondary workflow cannot replace the original refusal
                    # unless it independently supplies trusted FAILED evidence.
                    continue
                later.append((later_workflow, result))
                if 'FAILED' in result['check_outcomes'].values():
                    break
            if not later or not any(
                    'FAILED' in item['check_outcomes'].values()
                    for _, item in later):
                raise
            successful = set()
            check_ids = {}
            check_outcomes = {}
            for _, item in later:
                successful.update(item['successful_checks'])
                check_ids.update(item['check_ids'])
                check_outcomes.update(item['check_outcomes'])
            primary_workflow, primary = next(
                (workflow, item) for workflow, item in later
                if 'FAILED' in item['check_outcomes'].values())
            return {'workflow_id': primary['workflow_id'], 'run_id': primary['run_id'],
                    'run_attempt': primary['run_attempt'], 'run_number': primary['run_number'],
                    'successful_checks': sorted(successful), 'check_ids': check_ids,
                    'check_outcomes': check_outcomes, 'linux_required': linux_required,
                    'docs_only': docs_only,
                    'required_checks': sorted(item[field] for item in selected),
                    'workflow_path': primary_workflow['path'],
                    'workflow_name': primary_workflow['name']}
        results.append(result)
    successful, check_ids, check_outcomes = set(), {}, {}
    for result in results:
        successful.update(result['successful_checks'])
        check_ids.update(result['check_ids'])
        check_outcomes.update(result['check_outcomes'])
    primary, primary_workflow = results[0], selected[0]
    return {'workflow_id': primary['workflow_id'], 'run_id': primary['run_id'],
            'run_attempt': primary['run_attempt'], 'run_number': primary['run_number'],
            'successful_checks': sorted(successful), 'check_ids': check_ids,
            'check_outcomes': check_outcomes,
            'linux_required': linux_required, 'docs_only': docs_only,
            'required_checks': sorted(workflow[field] for workflow in selected),
            'workflow_path': primary_workflow['path'],
            'workflow_name': primary_workflow['name']}


def _not_run_checks(docs_only, scripts_only=False):
    """Display-only NOT_RUN names: ci_trust not_run_checks, plus skipped code checks."""
    ci_trust = _ci_trust()
    base = tuple(ci_trust['not_run_checks'])
    skipped = tuple(workflow['code_check'] for workflow in ci_trust['workflows']
                    if docs_only and workflow['code_check'] is not None)
    if scripts_only and not docs_only:
        skipped = tuple(workflow['code_check'] for workflow in ci_trust['workflows']
                        if workflow['code_check'] == _SCRIPTS_ONLY_SKIPPED_CODE_CHECK and
                        workflow['code_check'] not in base)
    return base + skipped


def collect_github_evidence(*, pr_number, result_sha, task_id, run_id,
                            contract_digest, executor_identity,
                            executor_session_ref, executor_native_session_id,
                            reviewer_task_id, common_dir, changed_paths=None,
                            task_record=None):
    """Read a public PR, formal review marker and exact-head required check runs."""
    if (type(pr_number) is not int or not 1 <= pr_number <= 9999999 or
            type(result_sha) is not str or _HEX40.fullmatch(result_sha) is None or
            type(task_id) is not str or re.fullmatch(r'[0-9a-f]{32}', task_id) is None or
            type(run_id) is not str or re.fullmatch(r'[0-9a-f]{32}', run_id) is None or
            type(contract_digest) is not str or _HEX64.fullmatch(contract_digest) is None):
        _stop('PR_NUMBER_INVALID')
    pr = _github_get(f'pulls/{pr_number}')
    is_open_pr = (type(pr) is dict and pr.get('state') == 'open' and
                  pr.get('merged') is False)
    is_merged_pr = (
        type(pr) is dict and pr.get('state') == 'closed' and pr.get('merged') is True and
        type(pr.get('merge_commit_sha')) is str and
        _HEX40.fullmatch(pr['merge_commit_sha']) is not None and
        type(pr.get('base')) is dict and pr['base'].get('ref') == 'main')
    if (type(pr) is not dict or not (is_open_pr or is_merged_pr) or
            type(pr.get('head')) is not dict or pr['head'].get('sha') != result_sha or
            type(pr.get('base')) is not dict or
            type(pr['base'].get('repo')) is not dict or
            pr['base']['repo'].get('full_name') != _trusted_slug() or
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
    binding_line = _review_binding_line(task_id, run_id, contract_digest, result_sha)
    review = None if reviewer_login is None else latest.get(reviewer_login.casefold(), (None, None))[1]
    # GitHub refuses APPROVE from a PR's own author, and every agent here posts
    # as the same account. The Reviewer therefore publishes its exact-HEAD
    # verdict as a PR conversation comment; its latest bound one is its
    # current disposition unless a newer PR review supersedes it. A later
    # verdict that names this HEAD without the binding line still supersedes
    # an earlier acceptance and then fails the binding gate.
    # Limitation: under one OS user and one shared GitHub account, nothing
    # binds a comment (or a PR review) to the Reviewer Session; any agent
    # with that account could post a bound ACCEPT. This gate is process
    # discipline, not adversarial independence, like the D6 registry itself.
    # The control that remains is the D6 Reviewer record (distinct agent,
    # Session and native Session, worktree at this HEAD) checked below.
    comments = _github_get(f'issues/{pr_number}/comments?per_page=100')
    if type(comments) is not list or len(comments) >= 100 or any(
            type(comment) is not dict for comment in comments):
        _stop('REVIEW_EVIDENCE_INVALID')
    verdict = None
    unsupported_family_comment = False
    codex_task_record = task_record
    codex_task_record_loaded = task_record is not None

    def load_completion_task_record():
        nonlocal codex_task_record, codex_task_record_loaded
        if not codex_task_record_loaded:
            from .task_store import TaskStore
            codex_task_record = TaskStore().get(task_id)
            codex_task_record_loaded = True
        return codex_task_record

    for comment in comments:
        login = (comment.get('user') or {}).get('login')
        body = comment.get('body')
        if (type(login) is not str or reviewer_login is None or
                login.casefold() != reviewer_login.casefold() or type(body) is not str):
            continue
        raw_lines = [line for line in body.splitlines() if line.strip()]
        lines = [line.strip() for line in raw_lines]
        if (not lines or not _VERDICT_ACTIVITY.fullmatch(lines[0]) or
                (binding_line not in lines and result_sha not in body)):
            continue
        # A padded marker or Verdict line is still a disposition for this
        # HEAD, but only the verbatim standard lines can be the standard form.
        marker = _VERDICT_MARKER.fullmatch(raw_lines[0])
        status = None if len(raw_lines) < 2 else _VERDICT_STATUS.fullmatch(raw_lines[1])
        standard = (marker is not None and status is not None and
                    status.group(1) == marker.group(2) and status.group(2) == result_sha)
        if standard and marker.group(2) == 'PASS':
            pass_task = load_completion_task_record()
            if (type(pass_task) is not dict or
                    not _review_family_compatible(
                        pass_task.get('developer'), reviewer.trusted_agent_id)):
                unsupported_family_comment = True
        if standard and marker.group(1) == 'Codex' and marker.group(2) == 'PASS':
            accepted_codex = False
            if reviewer.trusted_agent_id == 'codex-reviewer':
                try:
                    version_match = re.fullmatch(
                        r'Verdict: PASS \| Version: v([1-9][0-9]{0,3}) '
                        r'\| HEAD: [0-9a-f]{40}', raw_lines[1])
                    if version_match is None:
                        continue
                    version = int(version_match.group(1))
                    if not codex_task_record_loaded:
                        load_completion_task_record()
                    from . import review_result
                    classified = review_result._comment_verdict(
                        comment, head=result_sha,
                        version=version, task=codex_task_record)
                    claims = codex_task_record.get('review_dispatch_claim')
                    if type(claims) is dict:
                        claims = [claims]
                    task_binding = (codex_task_record.get('task_binding')
                                    if type(codex_task_record) is dict else None)
                    matching_claims = [claim for claim in claims
                                       if type(claim) is dict and
                                       claim.get('pr') == pr_number and
                                       claim.get('head_sha') == result_sha and
                                       claim.get('reviewer_task') == reviewer_task_id and
                                       claim.get('version') == version and
                                       claim.get('state') == 'COMPLETED' and
                                       type(claim.get('review_result')) is dict and
                                       claim['review_result'].get('task_id') == task_id and
                                       claim['review_result'].get('run_id') == run_id and
                                       type(task_binding) is dict and
                                       task_binding.get('contract_digest') == contract_digest and
                                       claim['review_result'].get('contract_digest') ==
                                       task_binding.get('contract_digest') and
                                       claim['review_result'].get('head') == result_sha and
                                       claim['review_result'].get('verdict') == 'PASS'
                                       ] if type(claims) is list else []
                    comment_lines = [line for line in body.splitlines() if line.strip()]
                    claim_lines = ([line for line in matching_claims[0]['review_result']['review_text'].splitlines()
                                    if line.strip()]
                                   if len(matching_claims) == 1 and
                                   type(matching_claims[0]['review_result'].get('review_text')) is str else None)
                    accepted_codex = (
                        type(codex_task_record) is dict and
                        codex_task_record.get('task_id') == task_id and
                        codex_task_record.get('run_id') == run_id and
                        type(classified) is tuple and len(classified) >= 3 and
                        classified[2] == 'REVIEW_PASS' and
                        _review_family_compatible(
                            codex_task_record.get('developer'), reviewer.trusted_agent_id) and
                        len(matching_claims) == 1 and claim_lines and
                        claim_lines == comment_lines)
                except (LLMToolError, KeyError, TypeError, ValueError, IndexError):
                    accepted_codex = False
            if not accepted_codex:
                continue  # Unbound or otherwise unverified Codex PASS stays advisory.
        # Anything else that claims to be a verdict for this HEAD is a
        # disposition; only the exact standard Claude PASS form accepts.
        if (standard and marker.group(1) == 'Claude' and marker.group(2) == 'PASS' and
                is_formal_claude_pass(
                    body, task_id=task_id, run_id=run_id,
                    contract_digest=contract_digest, result_sha=result_sha)):
            claude_task_record = load_completion_task_record()
            claude_family_compatible = (
                reviewer.trusted_agent_id == 'claude-reviewer' and
                type(claude_task_record) is dict and
                _review_family_compatible(claude_task_record.get('developer'),
                                          reviewer.trusted_agent_id))
        else:
            claude_family_compatible = False
        accepted = (standard and (
            (marker.group(1) == 'Claude' and marker.group(2) == 'PASS' and
             claude_family_compatible) or
            (marker.group(1) == 'Codex' and marker.group(2) == 'PASS' and
             accepted_codex)))
        if type(comment.get('id')) is not int:
            _stop('REVIEW_EVIDENCE_INVALID')
        created = _review_time(comment, 'created_at')
        updated = _review_time(comment, 'updated_at')
        # Verdicts are never edited; an edited one is a disposition that
        # cannot accept and is ordered by its last change, so it supersedes
        # any PASS posted before that edit.
        if updated != created:
            accepted = False
        key = (max(created, updated), comment['id'])
        if verdict is None or key[0] > verdict[0][0]:
            latest_outcomes = {accepted}
        elif key[0] == verdict[0][0]:
            latest_outcomes.add(accepted)
        if verdict is None or key > verdict[0]:
            verdict = (key, comment, accepted)
    review_source = None if review is None else 'PR_REVIEW'
    # Equal timestamps give no reliable order: across the two endpoints, or
    # between an accepting and a non-accepting verdict comment (the id is
    # creation order, not edit order).
    ordering_ambiguous = verdict is not None and (
        len(latest_outcomes) > 1 or
        (review is not None and verdict[0][0] == _review_time(review)))
    if ordering_ambiguous:
        verdict = (verdict[0], verdict[1], False)
    if verdict is not None and (review is None or verdict[0][0] > _review_time(review)):
        review = {'state': 'VERDICT_COMMENT' if verdict[2] else 'VERDICT_COMMENT_NOT_ACCEPTED',
                  'commit_id': result_sha, 'body': verdict[1]['body'],
                  'user': {'login': verdict[1]['user']['login']}}
        review_source = 'PR_COMMENT'
    formal_marker = re.compile(r'(?im)^\s*FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\s*$')
    review_body = '' if review is None or type(review.get('body')) is not str else review['body']
    body_lines = {line.strip() for line in review_body.splitlines()}
    review_binding_verified = binding_line in body_lines
    accepting_states = ('APPROVED', 'VERDICT_COMMENT')
    missing = []
    if unsupported_family_comment:
        missing.append('REVIEW_DEVELOPER_FAMILY_UNSUPPORTED')
    if ('REVIEW_DEVELOPER_FAMILY_UNSUPPORTED' not in missing and review is not None and
            (review.get('state') in accepting_states or review_source == 'PR_COMMENT')):
        completion_task = load_completion_task_record()
        developer = (completion_task.get('developer')
                     if type(completion_task) is dict else None)
        if not _review_family_compatible(developer, reviewer.trusted_agent_id):
            missing.append('REVIEW_DEVELOPER_FAMILY_UNSUPPORTED')
    if review is None:
        missing.append('FORMAL_REVIEW_MISSING')
    elif review.get('state') not in accepting_states or not formal_marker.search(review_body):
        missing.append('FORMAL_REVIEW_NOT_ACCEPTED')
    if review is not None and not review_binding_verified:
        missing.append('FORMAL_REVIEW_BINDING_MISMATCH')
    if any(item[1].get('state') == 'CHANGES_REQUESTED' for item in latest.values()):
        missing.append('FORMAL_REVIEW_NOT_ACCEPTED')
    if ordering_ambiguous:
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
        # #1241: company-gw only proposes a text diff and exposes no native
        # session; the PM applies it in the registered company-gw D6. There,
        # independence rests on the bound cross-family Reviewer on its own D6,
        # whose native session must differ from the PM session. Both sides are
        # compared as normalized identities (the Task stores `pm-<native>`).
        # Every other route, and any company-gw record that does not match
        # exactly, keeps the fail-closed rule.
        pm_session = None
        if executor_identity == 'company-gw':
            try:
                pm_session = _company_gw_text_proposal_pm_session(
                    load_completion_task_record(), executor_identity)
            except LLMToolError:
                pm_session = None
        pm_identity = _native_session_identity(pm_session)
        reviewer_identity = _native_session_identity(reviewer.native_session_id)
        if (pm_identity is None or reviewer_identity is None or
                reviewer_identity == pm_identity):
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
        'REVIEW_DEVELOPER_FAMILY_UNSUPPORTED',
        'REVIEWER_ACTOR_D6_BINDING_MISSING',
        'REVIEWER_ACTOR_D6_BINDING_MISMATCH',
        'REVIEWER_D6_HEAD_MISMATCH', 'REVIEWER_D6_HEAD_UNAVAILABLE',
    }
    reviewer_bound = (review is not None and review.get('state') in accepting_states and
                      bool(formal_marker.search(review_body)) and review_binding_verified and
                      not reviewer_gates.intersection(missing))
    ci = _collect_ci_evidence(pr_number=pr_number, result_sha=result_sha,
                              changed_paths=changed_paths, pr_merged=is_merged_pr,
                              pr_head_ref=pr['head'].get('ref'))
    successful = set(ci['successful_checks'])
    required_checks = set(ci['required_checks'])
    if not required_checks.issubset(successful):
        missing.append('REQUIRED_CI_MISSING')
        if 'FAILED' in ci.get('check_outcomes', {}).values():
            missing.append('REQUIRED_CI_FAILED')
        elif 'PENDING' in ci.get('check_outcomes', {}).values():
            missing.append('REQUIRED_CI_PENDING')
    return {'evidence': {'pr_number': pr_number, 'head_sha': result_sha,
            'pr_state': 'MERGED' if is_merged_pr else 'OPEN',
            'merge_commit_sha': pr.get('merge_commit_sha') if is_merged_pr else None,
            'formal_review_state': 'ACCEPTED' if reviewer_bound else 'UNVERIFIED',
            'review_actor': None if review is None else review['user']['login'].casefold(),
            'reviewer_github_login': None if reviewer.github_login is None else reviewer.github_login.casefold(),
            'review_commit_sha': None if review is None else review['commit_id'],
            'review_source': review_source,
            'reviewer_d6_identity': reviewer.trusted_agent_id,
            'reviewer_task_id': reviewer_task_id,
            'reviewer_session_ref': reviewer.session_ref,
            'review_session_identity': ('D6_NATIVE_SESSION_VERIFIED'
                if reviewer.native_session_id not in (None, 'UNKNOWN') else 'UNKNOWN'),
            'review_task_id': task_id, 'review_run_id': run_id,
            'review_contract_digest': contract_digest,
            'review_binding_verified': review_binding_verified,
            'required_checks': sorted(required_checks),
            'successful_checks': sorted(successful), 'ci_head_sha': result_sha,
            'not_run_checks': [{'name': name, 'status': 'NOT_RUN'}
                               for name in _not_run_checks(
                                   ci['docs_only'], _scripts_only_ci(changed_paths))],
            'ci_workflow_path': ci['workflow_path'],
            'ci_workflow_name': ci['workflow_name'],
            'ci_workflow_id': ci['workflow_id'], 'ci_run_id': ci['run_id'],
            'ci_run_attempt': ci['run_attempt'], 'ci_run_number': ci['run_number'],
            'ci_check_ids': ci['check_ids']},
            'missing_gates': sorted(set(missing))}
