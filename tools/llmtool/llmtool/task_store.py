"""Owner-private durable storage for llmtool PM-intake tasks.

This is a local task record store, not an execution queue or authorization
ledger. A task's logical PM session is an llmtool identifier; it is not a claim
that the selected provider exposes a resumable native session.
"""

import errno
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .action_contract import TaskBinding
from .diagnostics import validate_provider_usage
from .errors import LLMToolError
from .opencode_direct_developer import is_free_model
from .task_verification import parse_task_contract
from .test_output_excerpts import _emitted_sensitive
from .task_completion import validate_host_worker_receipt


def _added_diff_content(value):
    """Return added unified-diff hunk lines, excluding file headers."""
    added = []
    in_hunk = False
    previous = None
    for line in value.splitlines():
        if line.startswith('diff --git '):
            in_hunk = False
        elif line.startswith('@@ '):
            in_hunk = True
        elif (not in_hunk and line.startswith('+++ ') and previous is not None and
                previous.startswith('--- ')):
            pass
        elif in_hunk and line.startswith('+'):
            added.append(line[1:])
        previous = line
    return '\n'.join(added)

SCHEMA_VERSION = 2
MAX_RECORD_BYTES = 1024 * 1024
TASK_ID = re.compile(r'[0-9a-f]{32}')
D6_TASK_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,95}')
SESSION_ID = re.compile(r'pm-[a-z0-9-]{1,60}')
DEV_SESSION_ID = re.compile(r'dev-[a-z0-9-]{1,60}')
STATES = frozenset(('PM_PENDING', 'PM_RESPONDED', 'PM_UNAVAILABLE',
                     'INPUT_REJECTED', 'PM_RESPONSE_REJECTED'))
_FIELDS_V1 = frozenset((
    'schema_version', 'task_id', 'run_id', 'pm_session_id',
    'pm_provider_profile', 'pm_provider_type', 'pm_model',
    'backend_session_semantics', 'native_session_id', 'state',
    'created_at', 'updated_at', 'task_contract', 'task_binding',
    'request', 'request_sha256', 'response', 'usage', 'error_code',
    'record_sha256',
))
_FIELDS_V2 = _FIELDS_V1 | frozenset(('developer', 'pm_assignment'))
_FIELDS_V2_ROUTE = _FIELDS_V2 | frozenset(('pm_route_binding',))
_FIELDS_V2_COMPLETION = _FIELDS_V2 | frozenset(('completion',))
_FIELDS_V2_ROUTE_COMPLETION = _FIELDS_V2_ROUTE | frozenset(('completion',))
_V2_FIELD_SHAPES = (_FIELDS_V2_ROUTE, _FIELDS_V2_ROUTE_COMPLETION)
_FIELDS_V2_WITH_PROVENANCE = tuple(shape | frozenset(('pm_provenance',))
                                   for shape in _V2_FIELD_SHAPES)
_V2_BASE_SHAPES = (_FIELDS_V2, _FIELDS_V2_COMPLETION, _FIELDS_V2_ROUTE,
                   _FIELDS_V2_ROUTE_COMPLETION, *_FIELDS_V2_WITH_PROVENANCE)
_FIELDS_V2_WITH_DISPATCH_AUDIT = tuple(
    shape | frozenset(('dispatch_audit',)) for shape in _V2_BASE_SHAPES)
_V2_SHAPES = (_FIELDS_V2, _FIELDS_V2_COMPLETION, _FIELDS_V2_ROUTE,
              _FIELDS_V2_ROUTE_COMPLETION, *_FIELDS_V2_WITH_PROVENANCE,
              *_FIELDS_V2_WITH_DISPATCH_AUDIT)
_V2_REMOTE_TEST_SHAPES = tuple(shape | frozenset(('remote_test_receipts',))
                               for shape in _V2_SHAPES)
_V2_REMOTE_OUTCOME_SHAPES = tuple(shape | frozenset(('remote_test_outcomes',))
                                  for shape in _V2_SHAPES)
_V2_REMOTE_RECEIPT_OUTCOME_SHAPES = tuple(shape | frozenset((
    'remote_test_receipts', 'remote_test_outcomes')) for shape in _V2_SHAPES)
FIELDS = _FIELDS_V2_ROUTE_COMPLETION | frozenset((
    'dispatch_audit', 'completion_events', 'completion_event_tombstones',
    'remote_test_receipts', 'remote_test_outcomes', 'review_dispatch_claim',
    'review_repair_claims', 'high_debug', 'abandonment'))
_REVIEW_DISPATCH_CLAIM_FIELDS = frozenset((
    'executor_task', 'pr', 'head_sha', 'version', 'state', 'reviewer_task',
    'native_session_id', 'account_mode', 'claimed_at', 'updated_at'))
_REVIEW_DISPATCH_RESULT_FIELDS = frozenset((
    'task_id', 'run_id', 'contract_digest', 'head', 'verdict', 'review_text'))
_REVIEW_REPAIR_CLAIM_FIELDS = frozenset((
    'executor_task', 'pr', 'head_sha', 'version', 'attempt', 'state',
    'comment_id', 'packet_sha256', 'claimed_at'))
_REVIEW_REPAIR_OPTIONAL_FIELDS = frozenset(('repair_task_id', 'family',
                                             'high_debug_report_sha256'))
_V2_REPAIR_BASES = (*_V2_SHAPES, *_V2_REMOTE_TEST_SHAPES,
                    *_V2_REMOTE_OUTCOME_SHAPES,
                    *_V2_REMOTE_RECEIPT_OUTCOME_SHAPES)
_V2_REPAIR_OPTIONAL_BASES = (
    *_V2_REPAIR_BASES,
    *(shape | frozenset(('completion_events',)) for shape in _V2_REPAIR_BASES),
    *(shape | frozenset(('completion_events', 'completion_event_tombstones'))
      for shape in _V2_REPAIR_BASES),
    *(shape | frozenset(('review_dispatch_claim',)) for shape in _V2_REPAIR_BASES),
    *(shape | frozenset(('completion_events', 'review_dispatch_claim'))
      for shape in _V2_REPAIR_BASES),
    *(shape | frozenset(('completion_events', 'completion_event_tombstones',
                         'review_dispatch_claim')) for shape in _V2_REPAIR_BASES),
)
_V2_REPAIR_SHAPES = tuple(shape | frozenset(('review_repair_claims',))
                          for shape in _V2_REPAIR_OPTIONAL_BASES)
_HIGH_DEBUG_VALID_SHAPES = tuple(shape | frozenset(('high_debug',)) for shape in (
    *_V2_REPAIR_OPTIONAL_BASES, *_V2_REPAIR_SHAPES))
_HIGH_DEBUG_FIELDS = frozenset(('family', 'failed_corrections', 'early_reason', 'examined_sha',
    'd6', 'model_requested', 'model_observed', 'effort_observed', 'status',
    'report_sha256', 'report', 'usage', 'recorded_at'))
MAX_COMPLETION_EVENTS = 32
# Retention: when the event list is full, the oldest ACKED event is compacted
# into a tombstone that keeps its identity and stream position, so an acked
# event is never recreated or re-delivered. Only unacknowledged events can
# fill the list; that is the visible COMPLETION_EVENT_LIMIT state. Tombstones
# are never dropped: once they are full, the Task records no further events
# (COMPLETION_EVENT_HISTORY_LIMIT) and later results need a new Task or Run.
MAX_COMPLETION_TOMBSTONES = 256
_COMPLETION_TOMBSTONE_FIELDS = frozenset((
    'event_id', 'source', 'session_id', 'sequence', 'acked_at',
))
_COMPLETION_EVENT_FIELDS = frozenset((
    'event_id', 'task_id', 'run_id', 'pm_session_id', 'session_id', 'source', 'source_sha',
    'sequence', 'payload_sha256', 'state', 'received_at', 'consumed_at',
    'pm_decision_sha256', 'acked_at', 'dispatch_intent',
))
_GITHUB_EVENT_FIELDS = frozenset(('issue_number', 'comment_id', 'marker_type',
                                  'marker_status', 'merge_sha'))
_COMPLETION_INTENT_FIELDS = frozenset((
    'intent_id', 'intent_sha256', 'state', 'persisted_at',
    'receipt_sha256', 'confirmed_at',
))
_COMPLETION_SOURCES = frozenset(('DIRECT_D6_DEVELOPER', 'HOST_WORKER', 'REVIEWER',
                                 'EC2_REMOTE_TEST', 'GITHUB', 'HIGH_DEBUG'))
_REMOTE_TEST_RECEIPT_FIELDS = frozenset((
    'result', 'exit_code', 'source_sha', 'tree', 'fetch_head', 'modules',
    'identity', 'started_at', 'ended_at', 'elapsed_s', 'ran_total', 'final',
    'failures', 'log_bytes', 'log_sha256', 'remote_log_path', 'task_id',
    'run_id', 'executor_task', 'pm_session', 'command_digest', 'receipt_digest'))
_REMOTE_TEST_RECEIPT_FIELDS_WITH_EXCERPT = (
    _REMOTE_TEST_RECEIPT_FIELDS | frozenset(('failure_excerpt',)))
_REMOTE_TEST_FAILURE_EXCERPT_LIMIT = 4096
_REMOTE_TEST_FAILURE_EXCERPT_LINES = 40


def _valid_remote_test_failure_excerpt(value):
    if type(value) is not str or len(value.splitlines()) > _REMOTE_TEST_FAILURE_EXCERPT_LINES:
        return False
    try:
        return len(value.encode('utf-8')) <= _REMOTE_TEST_FAILURE_EXCERPT_LIMIT
    except UnicodeError:
        return False
MAX_REMOTE_TEST_OUTCOMES = 32
_REMOTE_TEST_OUTCOME_FIELDS = frozenset((
    'result', 'code', 'executor_task', 'sha', 'modules', 'at'))
_USAGE_FIELDS = frozenset(('prompt_tokens', 'completion_tokens', 'total_tokens'))
_OPTIONAL_USAGE_FIELDS = frozenset((
    'cached_prompt_tokens', 'reasoning_tokens', 'tool_calls', 'served_model'))
_SERVED_MODEL = re.compile(r'[A-Za-z0-9_./:-]{1,128}')
_DISPATCH_AUDIT_FIELDS = frozenset((
    'attempt_id', 'worker_key', 'action', 'decision', 'reason_code',
    'policy_sha256', 'at',
))
_DISPATCH_AUDIT_BINDING_FIELDS = frozenset((
    'assignment_id', 'source_sha', 'contract_digest',
))
_LEGACY_DEVELOPER_FIELDS = frozenset((
    'assignment_id', 'selection_source', 'state', 'provider_profile',
    'provider_type', 'model', 'backend_session_semantics', 'session_id',
    'source_sha', 'source_sha_binding', 'read_paths', 'write_paths', 'acceptance', 'context_sha256',
    'egress_basis', 'input_bytes', 'max_tokens',
    'artifact_format', 'artifact', 'artifact_sha256', 'usage', 'error_code',
    'created_at',
))
_DEVELOPER_FIELDS = _LEGACY_DEVELOPER_FIELDS | frozenset(('execution',))
_DEVELOPER_EXECUTOR_TASK_FIELDS = frozenset(('executor_task_id',))
_DEVELOPER_CALLBACK_FIELDS = _DEVELOPER_FIELDS | frozenset(('direct_result_callback',))
_DEVELOPER_SHAPES = (
    _LEGACY_DEVELOPER_FIELDS, _DEVELOPER_FIELDS, _DEVELOPER_CALLBACK_FIELDS,
    _LEGACY_DEVELOPER_FIELDS | frozenset(('owner_assignment_ref',)),
    _DEVELOPER_FIELDS | frozenset(('owner_assignment_ref',)),
    _DEVELOPER_CALLBACK_FIELDS | frozenset(('owner_assignment_ref',)),
)
_DEVELOPER_TASK_ID_SHAPES = tuple(
    shape | _DEVELOPER_EXECUTOR_TASK_FIELDS for shape in _DEVELOPER_SHAPES
)
_DEVELOPER_BASE_SHAPES = (*_DEVELOPER_SHAPES, *_DEVELOPER_TASK_ID_SHAPES)
_DEVELOPER_EXTENSION_SHAPES = tuple(
    shape | extension for shape in _DEVELOPER_BASE_SHAPES
    for extension in (frozenset(('normalizations',)),
                      frozenset(('rejected_output_capture',)),
                      frozenset(('normalizations', 'rejected_output_capture')))
)
_DEVELOPER_BLOCKED_REASON_SHAPES = tuple(
    shape | frozenset(('blocked_reason',))
    for shape in (*_DEVELOPER_BASE_SHAPES, *_DEVELOPER_EXTENSION_SHAPES)
)
_DEVELOPER_PROGRESS_SHAPES = tuple(
    shape | frozenset(('progress',))
    for shape in (*_DEVELOPER_BASE_SHAPES, *_DEVELOPER_EXTENSION_SHAPES,
                  *_DEVELOPER_BLOCKED_REASON_SHAPES)
)
_DIRECT_EXECUTION_FIELDS = frozenset((
    'status', 'native_session_id', 'process_exit_code', 'stdout_sha256',
    'elapsed_ms', 'cleanup_status', 'usage_source', 'source_bytes',
    'source_file_count', 'source_manifest_sha256',
))
_DIRECT_EXECUTION_BINDING_FIELDS = _DIRECT_EXECUTION_FIELDS | frozenset((
    'native_session_binding',
))
_DEVELOPER_EXECUTION_DIFF_FIELDS = frozenset(('diff_sha256', 'diff_base_sha'))
_DEVELOPER_EXECUTION_DIFF_SHAPES = (
    _DIRECT_EXECUTION_FIELDS | _DEVELOPER_EXECUTION_DIFF_FIELDS,
    _DIRECT_EXECUTION_BINDING_FIELDS | _DEVELOPER_EXECUTION_DIFF_FIELDS,
)
_DEVELOPER_REJECTION_CODES = frozenset((
    'DEVELOPER_OUTPUT_REJECTED', 'DEVELOPER_OUTPUT_INVALID_ENVELOPE',
    'DEVELOPER_OUTPUT_INVALID_DIFF', 'DEVELOPER_OUTPUT_OUT_OF_SCOPE',
    'DEVELOPER_OUTPUT_SENSITIVE', 'DEVELOPER_OUTPUT_INVALID_TEXT',
    'DEVELOPER_OUTPUT_TOO_LARGE', 'DEVELOPER_OUTPUT_IGNORED_FILES',
    'DEVELOPER_OUTPUT_APPLY_FAILED',
))
_PM_ASSIGNMENT_FIELDS = frozenset(('schema_version', 'route', 'risk', 'goal', 'acceptance'))
_PM_PROVENANCE_FIELDS = frozenset((
    'source', 'provider_invoked', 'native_session_id_status', 'usage_status',
    'session_restored', 'automatic_callback',
))
# Recovery identity of the live PM runtime that recorded a Task (#352 G6).
_PM_SESSION_FIELDS = frozenset(('pm_runtime', 'pm_native_session_id', 'pm_cwd'))
PM_NATIVE_SESSION_ID = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def _valid_pm_session_binding(record, provenance):
    """A declared PM session must be a well-formed, observed Claude Code session."""
    if not _PM_SESSION_FIELDS.issubset(provenance):
        return provenance['native_session_id_status'] == 'UNAVAILABLE'
    native = provenance['pm_native_session_id']
    cwd = provenance['pm_cwd']
    return (provenance['pm_runtime'] == 'claude-code' and
            provenance['native_session_id_status'] == 'LOCAL_SESSION_FILE_OBSERVED' and
            type(native) is str and PM_NATIVE_SESSION_ID.fullmatch(native) is not None and
            record['pm_session_id'] == 'pm-' + native and
            type(cwd) is str and 1 <= len(cwd) <= 1024 and cwd.startswith('/') and
            os.path.normpath(cwd) == cwd and
            not any(ord(char) < 0x20 or ord(char) == 0x7f for char in cwd))

# R5 technical observations live inside the existing TaskStore root. They are
# evidence only: this registry is deliberately separate from worker-dispatch.json
# and never participates in dispatch authorization.
WORKER_AVAILABILITY_SCHEMA_VERSION = 1
WORKER_AVAILABILITY_NAME = '.worker-availability.json'
WORKER_AVAILABILITY_LOCK_NAME = '.worker-availability.lock'
WORKER_AVAILABILITY_MAX_BYTES = 32 * 1024
WORKER_AVAILABILITY_MAX_AGE_DAYS = 30
TASK_UPDATE_LOCK_TIMEOUT_SECONDS = 2.0
TASK_UPDATE_LOCK_POLL_SECONDS = 0.01
_TASK_STORE_CONTENTION_ERRNOS = frozenset(
    value for value in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EBUSY, errno.EINTR,
                        errno.ENOLCK)
    if value is not None)
WORKER_KEYS = frozenset((
    'codex-developer', 'claude-developer', 'opencode-developer', 'agy',
    'company-gw', 'claude-reviewer',
))
WORKER_LAYERS = frozenset((
    'runtime', 'endpoint', 'authentication', 'inference', 'task_acceptance',
))
WORKER_OBSERVATION_STATUSES = frozenset(('PASS', 'FAIL', 'UNKNOWN'))
WORKER_OBSERVATION_SOURCES = frozenset((
    'LOCAL_OBSERVATION', 'AUTHORIZED_OPERATION', 'TEST_FIXTURE',
))
_WORKER_OBSERVATION_FIELDS = frozenset((
    'worker_key', 'layer', 'status', 'observed_at', 'fresh_until', 'source',
    'evidence_sha256',
))
_WORKER_AVAILABILITY_FIELDS = frozenset((
    'schema_version', 'observations', 'record_sha256',
))


def _fail(code='TASK_STORE_INVALID'):
    safe_code = code if (type(code) is str and
                         re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code)) else 'TASK_STORE_INVALID'
    if safe_code == 'TASK_STORE_BUSY':
        category = 'BUSY'
    elif safe_code == 'TASK_STORE_CONFLICT':
        category = 'CONFLICT'
    elif safe_code.endswith('UNAVAILABLE'):
        category = 'UNAVAILABLE'
    else:
        category = 'INVALID'
    error = LLMToolError('task store unavailable; details omitted')
    error.task_store_code = category
    error.task_store_error_code = safe_code
    raise error from None


def _is_contention_error(error):
    return error.errno in _TASK_STORE_CONTENTION_ERRNOS


def _validate_usage(value):
    fields = set(value) if type(value) is dict else set()
    if (type(value) is not dict or not _USAGE_FIELDS.issubset(fields) or
            not fields.issubset(_USAGE_FIELDS | _OPTIONAL_USAGE_FIELDS)):
        raise ValueError('invalid usage fields')
    for name in _OPTIONAL_USAGE_FIELDS & fields:
        if name == 'served_model':
            if (type(value[name]) is not str or
                    _SERVED_MODEL.fullmatch(value[name]) is None):
                raise ValueError('invalid served model')
            continue
        if type(value[name]) is not int or value[name] < 0:
            raise ValueError('invalid optional usage count')
    validate_provider_usage({name: value[name] for name in _USAGE_FIELDS})


class TaskStoreConflict(LLMToolError):
    """A compare-and-update precondition lost to another task writer."""

    def __init__(self, message='developer assignment already exists; duplicate dispatch refused'):
        super().__init__(message)
        self.task_store_code = 'CONFLICT'
        self.task_store_error_code = 'TASK_STORE_CONFLICT'


def new_id():
    return uuid.uuid4().hex


def default_root():
    return Path.home() / '.local' / 'state' / 'llmtool' / 'tasks'


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'


def _digest(record):
    value = {key: item for key, item in record.items() if key != 'record_sha256'}
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _safe_identifier(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _completion_timestamp(value):
    if type(value) is not str:
        _fail('COMPLETION_EVENT_INVALID')
    try:
        stamp = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        _fail('COMPLETION_EVENT_INVALID')
    if stamp.strftime('%Y-%m-%dT%H:%M:%SZ') != value:
        _fail('COMPLETION_EVENT_INVALID')
    return stamp


def _validate_completion_events(record):
    events = record['completion_events']
    if (record['schema_version'] != 2 or type(events) is not list or
            len(events) > MAX_COMPLETION_EVENTS):
        _fail('COMPLETION_EVENT_INVALID')
    ids, sequences, last_sequence = set(), set(), {}
    for event in events:
        github_fields = set(event) - _COMPLETION_EVENT_FIELDS if type(event) is dict else set()
        if (type(event) is not dict or
                (set(event) != _COMPLETION_EVENT_FIELDS and
                 not (set(event) == _COMPLETION_EVENT_FIELDS | _GITHUB_EVENT_FIELDS and
                      event.get('source') == 'GITHUB')) or
                not _safe_identifier(event['event_id'], TASK_ID) or
                event['event_id'] in ids or
                event['task_id'] != record['task_id'] or
                event['run_id'] != record['run_id'] or
                event['pm_session_id'] != record['pm_session_id'] or
                type(event['session_id']) is not str or
                re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:_-]{0,127}',
                             event['session_id']) is None or
                type(event['source']) is not str or
                event['source'] not in _COMPLETION_SOURCES or
                type(event['source_sha']) is not str or
                re.fullmatch(r'[0-9a-f]{40}', event['source_sha']) is None or
                type(event['sequence']) is not int or
                not 0 <= event['sequence'] <= 10**9 or
                type(event['payload_sha256']) is not str or
                re.fullmatch(r'[0-9a-f]{64}', event['payload_sha256']) is None or
                event['state'] not in ('PENDING', 'PM_CONSUMED', 'ACKED')):
            _fail('COMPLETION_EVENT_INVALID')
        if github_fields and (type(event.get('issue_number')) is not int or
                not 1 <= event['issue_number'] <= 9999999 or
                (event.get('comment_id') is not None and
                 (type(event['comment_id']) is not int or event['comment_id'] <= 0)) or
                type(event.get('marker_type')) is not str or
                re.fullmatch(r'[a-z][a-z0-9_]{0,63}', event['marker_type']) is None or
                type(event.get('marker_status')) is not str or
                re.fullmatch(r'[A-Z][A-Z0-9_]{0,31}', event['marker_status']) is None or
                (event.get('merge_sha') is not None and
                 re.fullmatch(r'[0-9a-f]{40}', event['merge_sha']) is None)):
            _fail('COMPLETION_EVENT_INVALID')
        ids.add(event['event_id'])
        sequence_key = (event['source'], event['session_id'], event['sequence'])
        if sequence_key in sequences:
            _fail('COMPLETION_EVENT_INVALID')
        sequences.add(sequence_key)
        stream = (event['source'], event['session_id'])
        if event['sequence'] <= last_sequence.get(stream, -1):
            _fail('COMPLETION_EVENT_INVALID')
        last_sequence[stream] = event['sequence']
        received_at = _completion_timestamp(event['received_at'])
        if event['state'] == 'PENDING':
            if (event['consumed_at'] is not None or
                    event['pm_decision_sha256'] is not None or
                    event['acked_at'] is not None or
                    event['dispatch_intent'] is not None):
                _fail('COMPLETION_EVENT_INVALID')
        else:
            consumed_at = _completion_timestamp(event['consumed_at'])
            if consumed_at < received_at:
                _fail('COMPLETION_EVENT_INVALID')
            if (type(event['pm_decision_sha256']) is not str or
                    re.fullmatch(r'[0-9a-f]{64}', event['pm_decision_sha256']) is None):
                _fail('COMPLETION_EVENT_INVALID')
            if event['state'] == 'ACKED':
                if _completion_timestamp(event['acked_at']) < consumed_at:
                    _fail('COMPLETION_EVENT_INVALID')
            elif event['acked_at'] is not None:
                _fail('COMPLETION_EVENT_INVALID')
        intent = event['dispatch_intent']
        if intent is not None:
            if (type(intent) is not dict or set(intent) != _COMPLETION_INTENT_FIELDS or
                    not _safe_identifier(intent['intent_id'], TASK_ID) or
                    type(intent['intent_sha256']) is not str or
                    re.fullmatch(r'[0-9a-f]{64}', intent['intent_sha256']) is None or
                    intent['state'] not in ('UNKNOWN', 'CONFIRMED')):
                _fail('COMPLETION_EVENT_INVALID')
            persisted_at = _completion_timestamp(intent['persisted_at'])
            if persisted_at < consumed_at:
                _fail('COMPLETION_EVENT_INVALID')
            if intent['state'] == 'UNKNOWN':
                if (intent['receipt_sha256'] is not None or
                        intent['confirmed_at'] is not None):
                    _fail('COMPLETION_EVENT_INVALID')
            else:
                if (type(intent['receipt_sha256']) is not str or
                        re.fullmatch(r'[0-9a-f]{64}', intent['receipt_sha256']) is None):
                    _fail('COMPLETION_EVENT_INVALID')
                if _completion_timestamp(intent['confirmed_at']) < persisted_at:
                    _fail('COMPLETION_EVENT_INVALID')
        if (event['state'] == 'ACKED' and intent is not None and
                intent['state'] != 'CONFIRMED'):
            _fail('COMPLETION_EVENT_INVALID')
        if (event['state'] == 'ACKED' and intent is not None and
                _completion_timestamp(event['acked_at']) <
                _completion_timestamp(intent['confirmed_at'])):
            _fail('COMPLETION_EVENT_INVALID')


def _validate_completion_tombstones(record):
    tombstones = record.get('completion_event_tombstones', [])
    if (type(tombstones) is not list or len(tombstones) > MAX_COMPLETION_TOMBSTONES or
            ('completion_event_tombstones' in record and not tombstones)):
        _fail('COMPLETION_EVENT_INVALID')
    live = {event['event_id'] for event in record['completion_events']}
    positions = {(event['source'], event['session_id'], event['sequence'])
                 for event in record['completion_events']}
    for tombstone in tombstones:
        if (type(tombstone) is not dict or set(tombstone) != _COMPLETION_TOMBSTONE_FIELDS or
                not _safe_identifier(tombstone['event_id'], TASK_ID) or
                tombstone['event_id'] in live or
                tombstone['source'] not in _COMPLETION_SOURCES or
                type(tombstone['session_id']) is not str or
                re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:_-]{0,127}',
                             tombstone['session_id']) is None or
                type(tombstone['sequence']) is not int or
                not 0 <= tombstone['sequence'] <= 10**9):
            _fail('COMPLETION_EVENT_INVALID')
        _completion_timestamp(tombstone['acked_at'])
        position = (tombstone['source'], tombstone['session_id'], tombstone['sequence'])
        if position in positions:
            _fail('COMPLETION_EVENT_INVALID')
        live.add(tombstone['event_id'])
        positions.add(position)


def _validate_remote_test_receipts(record):
    receipts = record.get('remote_test_receipts', [])
    if type(receipts) is not list or len(receipts) > 32:
        _fail('REMOTE_TEST_RECEIPT_INVALID')
    for receipt in receipts:
        if (type(receipt) is not dict or set(receipt) not in (
                _REMOTE_TEST_RECEIPT_FIELDS, _REMOTE_TEST_RECEIPT_FIELDS_WITH_EXCERPT) or
                ('failure_excerpt' in receipt and
                 (receipt.get('result') == 'PASS' or
                  not _valid_remote_test_failure_excerpt(
                      receipt['failure_excerpt']))) or
                receipt.get('result') not in ('PASS', 'FAIL', 'TIMEOUT', 'SHA_MISMATCH',
                                              'ADMISSION_DENIED', 'UNAVAILABLE', 'UNKNOWN') or
                type(receipt.get('exit_code')) not in (int, type(None)) or
                (receipt.get('exit_code') is not None and
                 not -127 <= receipt['exit_code'] <= 255) or
                receipt.get('task_id') != record.get('task_id') or
                receipt.get('run_id') != record.get('run_id') or
                type(receipt.get('executor_task')) is not str or
                re.fullmatch(r'[a-z0-9][a-z0-9-]{0,95}', receipt['executor_task']) is None or
                receipt.get('pm_session') != record.get('pm_session_id') or
                type(receipt.get('modules')) is not list or
                not 1 <= len(receipt['modules']) <= 32 or
                any(type(item) is not str or
                    re.fullmatch(r'test_[a-z0-9_]{1,80}', item) is None
                    for item in receipt['modules']) or
                receipt['modules'] != sorted(set(receipt['modules'])) or
                type(receipt.get('identity')) is not dict or
                set(receipt['identity']) != {'uid'} or
                (receipt['identity']['uid'] is not None and
                 (type(receipt['identity']['uid']) is not int or
                  receipt['identity']['uid'] < 0)) or
                any(type(receipt.get(field)) is not str or
                    re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', receipt[field]) is None
                    for field in ('started_at', 'ended_at')) or
                type(receipt.get('elapsed_s')) not in (int, float) or
                not math.isfinite(receipt['elapsed_s']) or receipt['elapsed_s'] < 0 or
                type(receipt.get('ran_total')) is not int or receipt['ran_total'] < 0 or
                type(receipt.get('final')) is not list or
                len(receipt['final']) > 64 or
                any(type(item) is not str or len(item) > 512 for item in receipt['final']) or
                type(receipt.get('failures')) is not list or len(receipt['failures']) > 10 or
                any(item not in receipt['modules'] for item in receipt['failures']) or
                type(receipt.get('log_bytes')) is not int or
                not 0 <= receipt['log_bytes'] <= 20_000_000 or
                type(receipt.get('log_sha256')) is not str or
                re.fullmatch(r'[0-9a-f]{64}', receipt['log_sha256']) is None or
                (receipt.get('remote_log_path') is not None and
                 (type(receipt['remote_log_path']) is not str or
                  re.fullmatch(r'/tmp/llmtool-remote-log\.[A-Za-z0-9]+/remote-test\.log',
                               receipt['remote_log_path']) is None)) or
                type(receipt.get('command_digest')) is not str or
                re.fullmatch(r'[0-9a-f]{64}', receipt['command_digest']) is None or
                type(receipt.get('receipt_digest')) is not str or
                re.fullmatch(r'[0-9a-f]{64}', receipt['receipt_digest']) is None):
            _fail('REMOTE_TEST_RECEIPT_INVALID')
        for field in ('source_sha', 'fetch_head', 'tree'):
            if receipt[field] is not None and (type(receipt[field]) is not str or
                    re.fullmatch(r'[0-9a-f]{40}', receipt[field]) is None):
                _fail('REMOTE_TEST_RECEIPT_INVALID')
        digest_input = dict(receipt)
        claimed = digest_input.pop('receipt_digest')
        digest = hashlib.sha256(_json_bytes(digest_input)).hexdigest()
        if claimed != digest:
            _fail('REMOTE_TEST_RECEIPT_INVALID')
        if (receipt['result'] == 'PASS' and
                (receipt['exit_code'] != 0 or receipt['fetch_head'] != receipt['source_sha'] or
                 type(receipt['identity'].get('uid')) is not int or receipt['identity']['uid'] == 0 or
                 receipt['ran_total'] <= 0 or receipt['failures'] or
                 receipt['tree'] is None or len(receipt['final']) < len(receipt['modules']) or
                 any(not (line.endswith('OK') or
                          re.fullmatch(r'OK \(skipped=[1-9][0-9]{0,5}\)', line))
                     for line in receipt['final'])) or
                (receipt['result'] == 'SHA_MISMATCH' and
                 receipt['fetch_head'] == receipt['source_sha'])):
            _fail('REMOTE_TEST_RECEIPT_INVALID')


def _validate_remote_test_outcomes(record):
    outcomes = record.get('remote_test_outcomes', [])
    if type(outcomes) is not list or len(outcomes) > MAX_REMOTE_TEST_OUTCOMES:
        _fail('REMOTE_TEST_OUTCOME_INVALID')
    for outcome in outcomes:
        if (type(outcome) is not dict or set(outcome) != _REMOTE_TEST_OUTCOME_FIELDS or
                outcome.get('result') not in ('ADMISSION_DENIED', 'TIMEOUT', 'UNAVAILABLE', 'UNKNOWN') or
                type(outcome.get('code')) is not str or
                re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', outcome['code']) is None or
                (outcome.get('executor_task') is not None and
                 (type(outcome['executor_task']) is not str or
                  D6_TASK_ID.fullmatch(outcome['executor_task']) is None)) or
                (outcome.get('sha') is not None and
                 (type(outcome['sha']) is not str or
                  re.fullmatch(r'[0-9a-f]{40}', outcome['sha']) is None)) or
                type(outcome.get('modules')) is not list or len(outcome['modules']) > 32 or
                any(type(module) is not str or
                    re.fullmatch(r'test_[a-z0-9_]{1,80}', module) is None
                    for module in outcome['modules']) or
                outcome['modules'] != sorted(set(outcome['modules'])) or
                type(outcome.get('at')) is not str or
                re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{6})?Z', outcome['at']) is None):
            _fail('REMOTE_TEST_OUTCOME_INVALID')


def _validate(record):
    keys = set(record) - {'abandonment'} if type(record) is dict else set()
    if (type(record) is not dict or
            (record.get('schema_version') == 1 and keys != _FIELDS_V1) or
            (record.get('schema_version') == 2 and keys not in
             (*_V2_SHAPES, *_V2_REMOTE_TEST_SHAPES,
              *_V2_REMOTE_OUTCOME_SHAPES, *_V2_REMOTE_RECEIPT_OUTCOME_SHAPES,
              *(shape | frozenset(('completion_events',))
                for shape in (*_V2_SHAPES, *_V2_REMOTE_TEST_SHAPES,
                              *_V2_REMOTE_OUTCOME_SHAPES,
                              *_V2_REMOTE_RECEIPT_OUTCOME_SHAPES)),
             *(shape | frozenset(('completion_events', 'completion_event_tombstones'))
                for shape in (*_V2_SHAPES, *_V2_REMOTE_TEST_SHAPES,
                              *_V2_REMOTE_OUTCOME_SHAPES,
                              *_V2_REMOTE_RECEIPT_OUTCOME_SHAPES)),
             *(shape | frozenset(('review_dispatch_claim',))
                for shape in (*_V2_SHAPES, *_V2_REMOTE_TEST_SHAPES,
                              *_V2_REMOTE_OUTCOME_SHAPES,
                              *_V2_REMOTE_RECEIPT_OUTCOME_SHAPES)),
             *(shape | frozenset(('completion_events', 'review_dispatch_claim'))
                for shape in (*_V2_SHAPES, *_V2_REMOTE_TEST_SHAPES,
                              *_V2_REMOTE_OUTCOME_SHAPES,
                              *_V2_REMOTE_RECEIPT_OUTCOME_SHAPES)),
             *(shape | frozenset(('completion_events', 'completion_event_tombstones',
                                  'review_dispatch_claim'))
                for shape in (*_V2_SHAPES, *_V2_REMOTE_TEST_SHAPES,
                              *_V2_REMOTE_OUTCOME_SHAPES,
                              *_V2_REMOTE_RECEIPT_OUTCOME_SHAPES)),
             *_V2_REPAIR_SHAPES, *_HIGH_DEBUG_VALID_SHAPES)) or
            record.get('schema_version') not in (1, 2)):
        _fail()
    if (type(record['schema_version']) is not int or record['schema_version'] not in (1, 2) or
            not _safe_identifier(record['task_id'], TASK_ID) or
            not _safe_identifier(record['run_id'], TASK_ID) or
            not _safe_identifier(record['pm_session_id'], SESSION_ID) or
            type(record['state']) is not str or record['state'] not in STATES or
            type(record['created_at']) is not str or type(record['updated_at']) is not str):
        _fail()
    if 'abandonment' in record:
        item = record['abandonment']
        if (type(item) is not dict or set(item) !=
                frozenset(('reason', 'abandoned_at', 'pm_session_id'))):
            _fail('TASK_ABANDONMENT_INVALID')
        reason = item['reason']
        abandoned_at = item['abandoned_at']
        try:
            parsed_time = (datetime.strptime(abandoned_at, '%Y-%m-%dT%H:%M:%SZ')
                           if type(abandoned_at) is str and re.fullmatch(
                               r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z',
                               abandoned_at) else None)
        except (TypeError, ValueError):
            parsed_time = None
        if (type(reason) is not str or not 1 <= len(reason) <= 500 or
                not reason.strip() or not all(char.isprintable() for char in reason) or
                _emitted_sensitive(reason) or parsed_time is None or
                not _safe_identifier(item['pm_session_id'], SESSION_ID) or
                item['pm_session_id'] != record['pm_session_id']):
            _fail('TASK_ABANDONMENT_INVALID')
    if 'completion_events' in record:
        _validate_completion_events(record)
        _validate_completion_tombstones(record)
    if 'high_debug' in record:
        entries = record['high_debug']
        if type(entries) is not list or len(entries) > 8:
            _fail('HIGH_DEBUG_INVALID')
        for item in entries:
            if (type(item) is not dict or set(item) != _HIGH_DEBUG_FIELDS or
                type(item.get('family')) is not str or re.fullmatch(r'[a-z0-9._-]{1,64}', item['family']) is None or
                type(item.get('examined_sha')) is not str or re.fullmatch(r'[0-9a-f]{40}', item['examined_sha']) is None or
                type(item.get('d6')) is not str or re.fullmatch(r'[a-z0-9][a-z0-9-]{0,95}', item['d6']) is None or
                type(item.get('model_requested')) is not str or type(item.get('model_observed')) is not str or
                type(item.get('effort_observed')) is not str or
                (item.get('failed_corrections') is not None and
                 (type(item['failed_corrections']) is not int or item['failed_corrections'] < 2)) or
                (item.get('early_reason') is not None and
                 (type(item['early_reason']) is not str or not 1 <= len(item['early_reason']) <= 300)) or
                ((item.get('failed_corrections') is None) == (item.get('early_reason') is None)) or
                type(item.get('report')) is not str or
                len(item['report'].encode('utf-8')) > 16384 or _emitted_sensitive(item['report']) or
                type(item.get('report_sha256')) is not str or re.fullmatch(r'[0-9a-f]{64}', item['report_sha256']) is None or
                hashlib.sha256(item['report'].encode('utf-8')).hexdigest() != item['report_sha256'] or
                (item.get('usage') != 'UNKNOWN' and type(item.get('usage')) is not dict) or
                type(item.get('recorded_at')) is not str or
                item.get('status') not in ('RCA_CONFIRMED', 'RCA_UNCONFIRMED', 'BLOCKED', 'REPORT_INVALID')):
                _fail('HIGH_DEBUG_INVALID')
    if 'review_dispatch_claim' in record:
        claim = record['review_dispatch_claim']
        claims = [claim] if type(claim) is dict else claim
        if type(claims) is not list or not claims:
            _fail('REVIEW_DISPATCH_CLAIM_INVALID')
        identities = set()
        for item in claims:
            if (type(item) is not dict or
                    set(item) not in (_REVIEW_DISPATCH_CLAIM_FIELDS,
                                      _REVIEW_DISPATCH_CLAIM_FIELDS |
                                      frozenset(('review_result',))) or
                    type(item.get('executor_task')) is not str or
                    not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,95}', item['executor_task']) or
                    type(item.get('pr')) is not int or not 1 <= item['pr'] <= 999999999 or
                    type(item.get('head_sha')) is not str or
                    re.fullmatch(r'[0-9a-f]{40}', item['head_sha']) is None or
                    type(item.get('version')) is not int or not 1 <= item['version'] <= 9999 or
                    item.get('state') not in ('CLAIMED', 'DISPATCHED', 'COMPLETED', 'UNKNOWN') or
                    (item.get('reviewer_task') is not None and
                     (type(item['reviewer_task']) is not str or
                      not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,95}', item['reviewer_task']))) or
                    (item.get('native_session_id') is not None and
                     (type(item['native_session_id']) is not str or
                      not 1 <= len(item['native_session_id']) <= 256 or
                      any(ord(c) < 0x20 or ord(c) == 0x7f for c in item['native_session_id']))) or
                    item.get('account_mode') not in ('EXPLICIT_CLAUDE_CONFIG_DIR',
                                                      'DEFAULT_SECOND_ACCOUNT',
                                                      'CODEX_CLI') or
                    type(item.get('claimed_at')) is not str or
                    type(item.get('updated_at')) is not str or
                    (item['state'] == 'CLAIMED' and
                     (item['reviewer_task'] is None or item['native_session_id'] is not None)) or
                    (item['state'] == 'DISPATCHED' and
                     (item['reviewer_task'] is None or item['native_session_id'] is None)) or
                    (item['state'] == 'COMPLETED' and
                     (item['reviewer_task'] is None or item['native_session_id'] is None or
                      'review_result' not in item)) or
                    (item['state'] != 'COMPLETED' and 'review_result' in item)):
                _fail('REVIEW_DISPATCH_CLAIM_INVALID')
            if item['state'] == 'COMPLETED':
                result = item['review_result']
                task_binding = record.get('task_binding')
                if (type(result) is not dict or set(result) != _REVIEW_DISPATCH_RESULT_FIELDS or
                        result.get('task_id') != record.get('task_id') or
                        result.get('run_id') != record.get('run_id') or
                        type(task_binding) is not dict or
                        result.get('contract_digest') != task_binding.get('contract_digest') or
                        result.get('head') != item['head_sha'] or
                        result.get('verdict') not in ('PASS', 'FIX', 'FAIL', 'BLOCKED') or
                        type(result.get('review_text')) is not str or
                        len(result['review_text'].encode('utf-8', 'replace')) > 65536):
                    _fail('REVIEW_DISPATCH_CLAIM_INVALID')
            identity = (item['pr'], item['head_sha'], item['version'])
            if identity in identities:
                _fail('REVIEW_DISPATCH_CLAIM_INVALID')
            identities.add(identity)
    if 'review_repair_claims' in record:
        claims = record['review_repair_claims']
        if type(claims) is not list or not 1 <= len(claims) <= 64:
            _fail('REVIEW_REPAIR_CLAIM_INVALID')
        identities = set()
        families = {}
        for item in claims:
            if (type(item) is not dict or
                    not _REVIEW_REPAIR_CLAIM_FIELDS.issubset(item) or
                    set(item) - _REVIEW_REPAIR_CLAIM_FIELDS - _REVIEW_REPAIR_OPTIONAL_FIELDS or
                    type(item.get('executor_task')) is not str or
                    not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,95}', item['executor_task']) or
                    type(item.get('pr')) is not int or not 1 <= item['pr'] <= 999999999 or
                    type(item.get('head_sha')) is not str or
                    re.fullmatch(r'[0-9a-f]{40}', item['head_sha']) is None or
                    type(item.get('version')) is not int or not 1 <= item['version'] <= 9999 or
                    type(item.get('attempt')) is not int or item['attempt'] not in (1, 2, 3) or
                    ('family' in item and
                     (type(item['family']) is not str or
                      re.fullmatch(r'[a-z0-9._-]{1,64}', item['family']) is None)) or
                    ('high_debug_report_sha256' in item and
                     (item.get('attempt') != 3 or
                      type(item['high_debug_report_sha256']) is not str or
                      re.fullmatch(r'[0-9a-f]{64}', item['high_debug_report_sha256']) is None)) or
                    item.get('state') not in ('CLAIMED', 'DISPATCHED', 'UNKNOWN', 'FAILED') or
                    (item.get('comment_id') is not None and
                     (type(item['comment_id']) is not int or item['comment_id'] < 0)) or
                    type(item.get('packet_sha256')) is not str or
                    re.fullmatch(r'[0-9a-f]{64}', item['packet_sha256']) is None or
                    type(item.get('claimed_at')) is not str or
                    re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ',
                                 item['claimed_at']) is None or
                    ('repair_task_id' in item and
                     (type(item['repair_task_id']) is not str or
                      not re.fullmatch(r'[0-9a-f]{32}', item['repair_task_id'])))):
                _fail('REVIEW_REPAIR_CLAIM_INVALID')
            identity = (item['pr'], item['head_sha'], item['version'], item['attempt'])
            if identity in identities:
                _fail('REVIEW_REPAIR_CLAIM_INVALID')
            identities.add(identity)
            family = item.get('family', f"pr-{item['pr']}")
            families.setdefault(family, []).append(item)
        for family_claims in families.values():
            if len(family_claims) > 3:
                _fail('REVIEW_REPAIR_CLAIM_INVALID')
            if any(item.get('attempt') != index
                   for index, item in enumerate(family_claims, 1)):
                _fail('REVIEW_REPAIR_CLAIM_INVALID')
            if len(family_claims) == 3:
                third = family_claims[2]
                if third.get('attempt') != 3:
                    _fail('REVIEW_REPAIR_CLAIM_INVALID')
                second_claimed_at = max(item['claimed_at'] for item in family_claims[:2])
                matching = [entry for entry in record.get('high_debug', [])
                            if type(entry) is dict and entry.get('family') ==
                            third.get('family', f"pr-{third['pr']}") and
                            entry.get('status') == 'RCA_CONFIRMED' and
                            entry.get('examined_sha') == third['head_sha'] and
                            type(entry.get('recorded_at')) is str and
                            entry['recorded_at'] > second_claimed_at and
                            entry.get('report_sha256') ==
                            third.get('high_debug_report_sha256')]
                if not matching:
                    _fail('REVIEW_REPAIR_CLAIM_INVALID')
    if 'remote_test_receipts' in record:
        _validate_remote_test_receipts(record)
    if 'remote_test_outcomes' in record:
        _validate_remote_test_outcomes(record)
    for key in ('created_at', 'updated_at'):
        try:
            stamp = datetime.strptime(record[key], '%Y-%m-%dT%H:%M:%SZ')
        except (TypeError, ValueError):
            _fail()
        if stamp.strftime('%Y-%m-%dT%H:%M:%SZ') != record[key]:
            _fail()
    if 'dispatch_audit' in record:
        audit = record['dispatch_audit']
        if type(audit) is not list or len(audit) > 64:
            _fail()
        seen_attempts = set()
        for entry in audit:
            if (type(entry) is not dict or set(entry) not in (
                    _DISPATCH_AUDIT_FIELDS,
                    _DISPATCH_AUDIT_FIELDS | _DISPATCH_AUDIT_BINDING_FIELDS,
                    _DISPATCH_AUDIT_FIELDS | _DISPATCH_AUDIT_BINDING_FIELDS |
                    frozenset(('authorization_id',))) or
                    type(entry['attempt_id']) is not str or
                    re.fullmatch(r'[0-9a-f]{32}', entry['attempt_id']) is None or
                    entry['attempt_id'] in seen_attempts or
                    type(entry['worker_key']) is not str or
                    re.fullmatch(r'[a-z][a-z0-9-]{0,63}', entry['worker_key']) is None or
                    entry['action'] not in (
                        'DISPATCH', 'RETRY', 'REASSIGN', 'RECOVERY', 'REPAIR',
                        'TIMEOUT_CONTINUATION', 'REVIEWER_HANDOFF', 'REVIEWER_RETRY') or
                    entry['decision'] not in ('ALLOWED', 'BLOCKED') or
                    type(entry['reason_code']) is not str or
                    re.fullmatch(r'[A-Z0-9_]{1,64}', entry['reason_code']) is None or
                    (entry['policy_sha256'] is not None and
                     (type(entry['policy_sha256']) is not str or
                      re.fullmatch(r'[0-9a-f]{64}', entry['policy_sha256']) is None))):
                _fail()
            if _DISPATCH_AUDIT_BINDING_FIELDS.issubset(entry):
                if (type(entry['assignment_id']) is not str or
                        re.fullmatch(r'[0-9a-f]{32}', entry['assignment_id']) is None or
                        type(entry['source_sha']) is not str or
                        re.fullmatch(r'[0-9a-f]{40}', entry['source_sha']) is None or
                        type(entry['contract_digest']) is not str or
                        re.fullmatch(r'[0-9a-f]{64}', entry['contract_digest']) is None):
                    _fail()
            if 'authorization_id' in entry and (
                    type(entry['authorization_id']) is not str or
                    re.fullmatch(r'[0-9a-f]{32}', entry['authorization_id']) is None):
                _fail()
            try:
                stamp = datetime.strptime(entry['at'], '%Y-%m-%dT%H:%M:%SZ')
            except (TypeError, ValueError):
                _fail()
            if stamp.strftime('%Y-%m-%dT%H:%M:%SZ') != entry['at']:
                _fail()
            seen_attempts.add(entry['attempt_id'])
    for key in ('pm_provider_profile', 'pm_provider_type', 'pm_model',
                'backend_session_semantics', 'error_code'):
        value = record[key]
        if value is not None and (type(value) is not str or len(value) > 256):
            _fail()
    if (type(record['pm_provider_profile']) is not str or
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', record['pm_provider_profile']) is None):
        _fail()
    if record['pm_provider_type'] is not None and re.fullmatch(
            r'[a-z][a-z0-9-]{0,63}', record['pm_provider_type']) is None:
        _fail()
    if record['pm_model'] is not None and re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}', record['pm_model']) is None:
        _fail()
    if 'pm_route_binding' in record:
        route = record['pm_route_binding']
        if route is not None:
            if (type(route) is not dict or set(route) != {'type', 'model', 'base_url'} or
                    route['type'] != record['pm_provider_type'] or
                    route['model'] != record['pm_model']):
                _fail()
            base_url = route['base_url']
            if base_url is not None:
                try:
                    if type(base_url) is not str or len(base_url) > 2048:
                        _fail()
                    parsed = urlsplit(base_url)
                    hostname = parsed.hostname
                    if (any(c.isspace() or ord(c) < 32 for c in base_url) or
                            parsed.scheme.lower() not in ('http', 'https') or not hostname or
                            parsed.username is not None or parsed.password is not None or
                            parsed.path or parsed.query or parsed.fragment):
                        _fail()
                    hostname = hostname.rstrip('.').encode('idna').decode('ascii').lower()
                    port = parsed.port
                    if port is not None and not 1 <= port <= 65535:
                        _fail()
                    authority_host = '[' + hostname + ']' if ':' in hostname else hostname
                    scheme = parsed.scheme.lower()
                    if port is not None and not (scheme == 'http' and port == 80 or
                                                 scheme == 'https' and port == 443):
                        authority_host += ':' + str(port)
                    if base_url != scheme + '://' + authority_host:
                        _fail()
                except (AttributeError, UnicodeError, ValueError):
                    _fail()
        elif record['state'] == 'PM_RESPONDED':
            _fail()
    if record['native_session_id'] is not None:
        _fail()
    if record['backend_session_semantics'] not in (
            'EPHEMERAL_SINGLE_CALL', 'NO_NATIVE_SESSION_ID_EXPOSED', 'NOT_STARTED',
            'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL'):
        _fail()
    contract_raw = record['task_contract']
    if type(contract_raw) is not dict:
        _fail()
    try:
        contract = parse_task_contract(json.dumps(contract_raw, ensure_ascii=True,
                                                   separators=(',', ':')))
        binding = TaskBinding(**record['task_binding'])
    except Exception:
        _fail()
    if (contract.task_id != record['task_id'] or
            binding != contract.binding(record['run_id'])):
        _fail()
    state = record['state']
    request = record['request']
    response = record['response']
    if request is not None:
        try:
            request_size = len(request.encode('utf-8'))
        except (AttributeError, UnicodeError):
            _fail()
        if type(request) is not str or request_size > 64 * 1024:
            _fail()
    if response is not None:
        try:
            response_size = len(response.encode('utf-8'))
        except (AttributeError, UnicodeError):
            _fail()
        if type(response) is not str or response_size > 64 * 1024:
            _fail()
    if any(_emitted_sensitive(text) for text in (request, response) if text is not None):
        _fail()
    if state == 'INPUT_REJECTED':
        if record['request_sha256'] is not None:
            _fail()
    elif (type(record['request_sha256']) is not str or
          re.fullmatch(r'[0-9a-f]{64}', record['request_sha256']) is None):
        _fail()
    if request is not None and hashlib.sha256(request.encode('utf-8')).hexdigest() != record['request_sha256']:
        _fail()
    if type(record['usage']) is dict:
        try:
            _validate_usage(record['usage'])
        except Exception:
            _fail()
    elif record['usage'] is not None:
        _fail()
    if record['error_code'] is not None and re.fullmatch(r'[A-Z0-9_]{1,64}', record['error_code']) is None:
        _fail()
    if record['schema_version'] == 2:
        assignment = record['pm_assignment']
        if assignment is not None:
            if (type(assignment) is not dict or set(assignment) != _PM_ASSIGNMENT_FIELDS or
                    type(assignment['schema_version']) is not int or assignment['schema_version'] != 1 or
                    assignment['route'] not in ('company-gw', 'codex', 'opencode', 'agy', 'none') or
                    assignment['risk'] not in ('LOW', 'OTHER') or
                    type(assignment['goal']) is not str or not assignment['goal'] or
                    len(assignment['goal'].encode('utf-8')) > 512 or
                    _emitted_sensitive(assignment['goal']) or
                    type(assignment['acceptance']) is not list or
                    not 1 <= len(assignment['acceptance']) <= 16 or
                    any(type(item) is not str or not item or len(item.encode('utf-8')) > 512 or
                        _emitted_sensitive(item) for item in assignment['acceptance'])):
                _fail()
        if 'pm_provenance' in record:
            provenance = record['pm_provenance']
            def unique_response_pairs(pairs):
                response_object = {}
                for key, value in pairs:
                    if key in response_object:
                        raise ValueError('duplicate JSON key')
                    response_object[key] = value
                return response_object

            try:
                response_object = json.loads(
                    record['response'], object_pairs_hook=unique_response_pairs,
                    parse_constant=lambda _: _fail())
                response_assignment = response_object.get('developer_assignment')
            except Exception:
                _fail()
            if (type(provenance) is not dict or
                    set(provenance) not in (_PM_PROVENANCE_FIELDS,
                                            _PM_PROVENANCE_FIELDS | _PM_SESSION_FIELDS) or
                    provenance['source'] != 'ACTIVE_PM_RUNTIME' or
                    provenance['provider_invoked'] is not False or
                    not _valid_pm_session_binding(record, provenance) or
                    provenance['usage_status'] != 'UNKNOWN' or
                    provenance['session_restored'] is not False or
                    provenance['automatic_callback'] is not False or
                    record['pm_provider_profile'] != 'active-pm-runtime' or
                    record['pm_provider_type'] not in ('active-pm-runtime', 'codex-cli') or
                    record['pm_model'] is not None or
                    record['pm_route_binding'] != {
                        'type': record['pm_provider_type'], 'model': None, 'base_url': None} or
                    record['backend_session_semantics'] != 'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL' or
                    record['native_session_id'] is not None or
                    record['state'] != 'PM_RESPONDED' or
                    record['request'] is None or not record['response'] or
                    record['request_sha256'] is None or record['usage'] is not None or
                    record['error_code'] is not None or
                    type(assignment) is not dict or
                    assignment.get('route') not in ('company-gw', 'opencode', 'codex', 'agy') or
                    assignment.get('risk') != 'LOW' or
                    type(response_object) is not dict or
                    set(response_object) != {
                        'schema_version', 'summary', 'developer_assignment'} or
                    type(response_object['schema_version']) is not int or
                    response_object['schema_version'] != 1 or
                    type(response_object['summary']) is not str or
                    _emitted_sensitive(response_object['summary']) or
                    type(response_assignment) is not dict or
                    assignment != {'schema_version': 1, **response_assignment}):
                _fail()
        elif record['backend_session_semantics'] == 'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL':
            _fail()
        developer = record['developer']
        if developer is not None:
            assignment_shapes = (*_DEVELOPER_BASE_SHAPES,
                                 *_DEVELOPER_EXTENSION_SHAPES,
                                 *_DEVELOPER_BLOCKED_REASON_SHAPES,
                                 *_DEVELOPER_PROGRESS_SHAPES)
            if type(developer) is not dict or set(developer) not in assignment_shapes:
                _fail()
            if ('executor_task_id' in developer and
                    not _safe_identifier(developer['executor_task_id'], D6_TASK_ID)):
                _fail()
            if (not _safe_identifier(developer['assignment_id'], TASK_ID) or
                    developer['selection_source'] not in ('PM', 'USER') or
                    developer['state'] not in ('DEVELOPER_PENDING', 'DEVELOPER_DISPATCHING',
                                               'DEVELOPER_RESPONDED',
                                               'DEVELOPER_UNAVAILABLE', 'DEVELOPER_OUTCOME_UNKNOWN',
                                               'DEVELOPER_OUTPUT_REJECTED', 'BLOCKED') or
                    developer['provider_profile'] not in ('company-gw', 'codex', 'opencode', 'claude', 'agy') or
                    developer['provider_type'] not in (
                        None, 'openai-compatible', 'codex-cli', 'opencode-cli', 'agy-cli') or
                    developer['backend_session_semantics'] not in (
                        'NO_NATIVE_SESSION_ID_EXPOSED', 'CLI_REPORTED_SESSION_ID') or
                    not _safe_identifier(developer['session_id'], DEV_SESSION_ID) or
                    type(developer['source_sha']) is not str or
                    re.fullmatch(r'[0-9a-f]{40}', developer['source_sha']) is None or
                    developer['source_sha_binding'] not in
                    ('UNVERIFIED_CALLER_INPUT', 'D6_REGISTRY_OBSERVED') or
                    type(developer['context_sha256']) is not str or
                    re.fullmatch(r'[0-9a-f]{64}', developer['context_sha256']) is None or
                    type(developer['egress_basis']) is not str or
                    not developer['egress_basis'] or len(developer['egress_basis'].encode('utf-8')) > 512 or
                    _emitted_sensitive(developer['egress_basis']) or
                    type(developer['input_bytes']) is not int or
                    not 1 <= developer['input_bytes'] <= 64 * 1024 or
                    (developer['max_tokens'] is not None and
                     (type(developer['max_tokens']) is not int or
                      not 1 <= developer['max_tokens'] <= 1_000_000)) or
                    developer['artifact_format'] != 'unified_diff' or
                    type(developer['created_at']) is not str):
                _fail()
            if 'owner_assignment_ref' in developer:
                from .worker_dispatch import _OWNER_COMMENT_REF
                ref = developer['owner_assignment_ref']
                if (developer['provider_profile'] not in ('claude', 'agy') or
                        type(ref) is not str or _OWNER_COMMENT_REF.fullmatch(ref) is None):
                    _fail()
            if developer['provider_profile'] == 'agy' and 'owner_assignment_ref' not in developer:
                _fail()
            if 'normalizations' in developer:
                tokens = developer['normalizations']
                if (developer['state'] != 'DEVELOPER_RESPONDED' or
                        type(tokens) is not list or len(tokens) > 5 or
                        any(token not in ('OUTER_FENCE_STRIPPED', 'DIFF_HEADER_ADDED',
                                          'BARE_BLANK_CONTEXT_SPACED',
                                          'HUNK_COUNTS_RECOUNTED',
                                          'HUNK_REANCHORED') for token in tokens) or
                        len(tokens) != len(set(tokens))):
                    _fail()
            if 'rejected_output_capture' in developer:
                capture = developer['rejected_output_capture']
                if (developer['state'] != 'DEVELOPER_OUTPUT_REJECTED' or
                        type(capture) is not dict or set(capture) != {'ref', 'sha256'} or
                        type(capture['ref']) is not str or
                        re.fullmatch(r'rejected-developer-output/[0-9a-f]{32}-[0-9a-f]{16}\.txt',
                                     capture['ref']) is None or
                        type(capture['sha256']) is not str or
                        re.fullmatch(r'[0-9a-f]{64}', capture['sha256']) is None):
                    _fail()
            if (developer['provider_profile'] == 'company-gw' and
                    (developer['provider_type'] not in (None, 'openai-compatible') or
                     developer['backend_session_semantics'] != 'NO_NATIVE_SESSION_ID_EXPOSED')):
                _fail()
            if (developer['provider_profile'] == 'opencode' and
                    (developer['provider_type'] not in (None, 'opencode-cli') or
                     developer['backend_session_semantics'] != 'CLI_REPORTED_SESSION_ID' or
                     not (developer['model'] is None or is_free_model(developer['model'])))):
                _fail()
            if (developer['provider_profile'] == 'codex' and
                    (developer['provider_type'] not in (None, 'codex-cli') or
                     developer['backend_session_semantics'] not in
                        ('NO_NATIVE_SESSION_ID_EXPOSED', 'CLI_REPORTED_SESSION_ID') or
                     developer['max_tokens'] is not None)):
                _fail()
            if (developer['provider_profile'] == 'agy' and
                    (developer['provider_type'] not in (None, 'agy-cli') or
                     developer['backend_session_semantics'] != 'CLI_REPORTED_SESSION_ID' or
                     developer['max_tokens'] is not None)):
                _fail()
            if (developer['provider_profile'] == 'claude' and
                    (developer['provider_type'] is not None or
                     developer['backend_session_semantics'] != 'NO_NATIVE_SESSION_ID_EXPOSED' or
                     developer['state'] != 'DEVELOPER_PENDING' or
                     'owner_assignment_ref' not in developer)):
                _fail()
            if 'execution' in developer:
                execution = developer['execution']
                if execution is not None:
                    if (developer['provider_profile'] not in ('opencode', 'codex', 'agy') or
                            type(execution) is not dict or
                            set(execution) not in (
                                _DIRECT_EXECUTION_FIELDS, _DIRECT_EXECUTION_BINDING_FIELDS,
                                *_DEVELOPER_EXECUTION_DIFF_SHAPES) or
                            ('native_session_binding' in execution and
                             (developer['provider_profile'] != 'codex' or
                              type(execution['native_session_binding']) is not str or
                              re.fullmatch(r'[A-Z0-9_]{1,64}',
                                           execution['native_session_binding']) is None)) or
                            execution['status'] not in (
                                ('SUCCEEDED', 'FAILED', 'UNKNOWN')
                                if developer['provider_profile'] == 'opencode' else
                                ('DONE', 'FAILED', 'UNKNOWN', 'BLOCKED', 'TIMEOUT')) or
                            (execution['native_session_id'] is not None and
                             (type(execution['native_session_id']) is not str or
                              re.fullmatch(
                                  r'ses_[A-Za-z0-9]{1,100}' if developer['provider_profile'] == 'opencode'
                                  else r'[A-Za-z0-9_-]{8,128}' if developer['provider_profile'] == 'agy'
                                  else r'[0-9a-fA-F-]{16,64}',
                                  execution['native_session_id']) is None)) or
                            (execution['process_exit_code'] is not None and
                             (type(execution['process_exit_code']) is not int or
                              not -127 <= execution['process_exit_code'] <= 255)) or
                            (execution['stdout_sha256'] is not None and
                             (type(execution['stdout_sha256']) is not str or
                              re.fullmatch(r'[0-9a-f]{64}', execution['stdout_sha256']) is None)) or
                            (execution['elapsed_ms'] is not None and
                             (type(execution['elapsed_ms']) is not int or
                              not 0 <= execution['elapsed_ms'] <= 86_400_000)) or
                            (execution['source_bytes'] is not None and
                             (type(execution['source_bytes']) is not int or
                              not 0 <= execution['source_bytes'] <= 2 * 1024 * 1024)) or
                            (execution['source_file_count'] is not None and
                             (type(execution['source_file_count']) is not int or
                              not 1 <= execution['source_file_count'] <= 32)) or
                            (execution['source_manifest_sha256'] is not None and
                             (type(execution['source_manifest_sha256']) is not str or
                              re.fullmatch(r'[0-9a-f]{64}',
                                           execution['source_manifest_sha256']) is None)) or
                            execution['cleanup_status'] not in ('CONFIRMED', 'UNKNOWN') or
                            execution['usage_source'] not in ('CLI_REPORTED', 'UNKNOWN')):
                        _fail()
                    if set(execution) in _DEVELOPER_EXECUTION_DIFF_SHAPES and (
                            developer.get('error_code') != 'CODEX_TIMEOUT_WITH_CHANGES' or
                            developer.get('provider_profile') != 'codex' or
                            execution['status'] != 'TIMEOUT' or
                            type(execution.get('diff_sha256')) is not str or
                            re.fullmatch(r'[0-9a-f]{64}', execution['diff_sha256']) is None or
                            type(execution.get('diff_base_sha')) is not str or
                            re.fullmatch(r'[0-9a-f]{40}', execution['diff_base_sha']) is None or
                            execution['diff_base_sha'] != developer['source_sha']):
                        _fail()
                    if execution['status'] in ('SUCCEEDED', 'DONE') and (
                            execution['native_session_id'] is None or
                            execution['process_exit_code'] != 0 or
                            execution['stdout_sha256'] is None or
                            (developer['provider_profile'] == 'opencode' and
                             (execution['source_bytes'] is None or
                              execution['source_file_count'] is None or
                              execution['source_manifest_sha256'] is None)) or
                            execution['cleanup_status'] != 'CONFIRMED'):
                        _fail()
                    source_observation = (execution['source_bytes'],
                                         execution['source_file_count'],
                                         execution['source_manifest_sha256'])
                    if any(value is None for value in source_observation) and not all(
                            value is None for value in source_observation):
                        _fail()
            elif developer['provider_profile'] == 'opencode':
                _fail()
            try:
                stamp = datetime.strptime(developer['created_at'], '%Y-%m-%dT%H:%M:%SZ')
            except (TypeError, ValueError):
                _fail()
            if stamp.strftime('%Y-%m-%dT%H:%M:%SZ') != developer['created_at']:
                _fail()
            model = developer['model']
            if model is not None and (type(model) is not str or len(model) > 256):
                _fail()
            for field in ('read_paths', 'write_paths'):
                values = developer[field]
                if (type(values) is not list or not values or len(values) > 32 or
                        any(type(item) is not str or not item or len(item) > 512 or
                            item.startswith('/') or '\\' in item or
                            any(part in ('', '.', '..') for part in item.split('/')) or
                            any(ord(char) < 0x20 or ord(char) >= 0x7f for char in item)
                            for item in values) or values != sorted(set(values))):
                    _fail()
            if not set(developer['write_paths']).issubset(developer['read_paths']):
                _fail()
            acceptance = developer['acceptance']
            if (type(acceptance) is not list or not 1 <= len(acceptance) <= 16 or
                    any(type(item) is not str or not item or len(item.encode('utf-8')) > 512
                        for item in acceptance)):
                _fail()
            artifact = developer['artifact']
            artifact_sha = developer['artifact_sha256']
            if artifact is not None:
                if (type(artifact) is not str or len(artifact.encode('utf-8')) > 64 * 1024 or
                        type(artifact_sha) is not str or
                        hashlib.sha256(artifact.encode('utf-8')).hexdigest() != artifact_sha or
                        _emitted_sensitive(_added_diff_content(artifact))):
                    _fail()
            elif artifact_sha is not None:
                _fail()
            usage = developer['usage']
            if usage is not None:
                try:
                    _validate_usage(usage)
                except Exception:
                    _fail()
            if developer['error_code'] is not None and (
                    type(developer['error_code']) is not str or
                    re.fullmatch(r'[A-Z0-9_]{1,64}', developer['error_code']) is None):
                _fail()
            dev_state = developer['state']
            if 'blocked_reason' in developer:
                reason = developer['blocked_reason']
                execution = developer.get('execution')
                agy_isolation_block = (
                    developer['provider_profile'] == 'agy' and
                    developer['state'] == 'BLOCKED' and
                    developer['error_code'] == 'BLOCKED_WRITE_ISOLATION' and
                    reason in ('BLOCKED_WRITE_ISOLATION', 'UNSUPPORTED_BOUNDARY',
                               'BOUNDARY_PREPARATION_FAILED', 'BOUNDARY_UNVERIFIED'))
                agy_dispatch_block = (
                    developer['provider_profile'] == 'agy' and
                    developer['state'] == 'BLOCKED' and
                    developer['error_code'] == 'AGY_DISPATCH_DENIED' and
                    reason == 'OWNER_DISPATCH_DENIED')
                codex_block = (
                    developer['provider_profile'] == 'codex' and
                    developer['state'] == 'DEVELOPER_OUTCOME_UNKNOWN' and
                    developer['error_code'] == 'CODEX_BLOCKED' and
                    type(execution) is dict and execution.get('status') == 'BLOCKED')
                if (not (agy_isolation_block or agy_dispatch_block or codex_block) or
                        type(reason) is not str or
                        len(reason.encode('utf-8')) > 2048 or
                        any((ord(char) < 32 and char not in '\t\n\r') or
                            127 <= ord(char) <= 159 or
                            unicodedata.category(char) == 'Cf'
                            for char in reason) or _emitted_sensitive(reason)):
                    _fail()
            expected_provider = {
                'company-gw': 'openai-compatible',
                'codex': 'codex-cli',
                'opencode': 'opencode-cli',
                'agy': 'agy-cli',
            }.get(developer['provider_profile'])
            token_cap_required = not (
                developer['provider_profile'] in ('codex', 'agy') and
                developer['provider_type'] in ('codex-cli', 'agy-cli'))
            if dev_state == 'DEVELOPER_PENDING' and (
                    developer['provider_type'] is not None or developer['model'] is not None or
                    artifact is not None or usage is not None or developer['error_code'] is not None):
                _fail()
            if 'progress' in developer:
                progress = developer['progress']
                if (developer['provider_profile'] != 'agy' or
                        developer['state'] != 'BLOCKED' or type(progress) is not dict or
                        set(progress) != {'state', 'head_sha', 'changed_files',
                                          'unfinished_checks', 'session_id'} or
                        progress.get('state') != 'QUOTA_EXHAUSTED' or
                        (progress.get('head_sha') != 'UNKNOWN' and
                         (type(progress.get('head_sha')) is not str or
                          re.fullmatch(r'[0-9a-f]{40}', progress['head_sha']) is None)) or
                        type(progress.get('changed_files')) is not list or
                        len(progress['changed_files']) > 200 or
                        any(type(path) is not str or len(path) > 512
                            for path in progress['changed_files']) or
                        type(progress.get('unfinished_checks')) is not list or
                        any(type(item) is not str or len(item) > 128
                            for item in progress['unfinished_checks']) or
                        (progress.get('session_id') is not None and
                         (type(progress['session_id']) is not str or
                          re.fullmatch(r'[A-Za-z0-9_-]{8,128}', progress['session_id']) is None))):
                    _fail()
            if dev_state == 'DEVELOPER_DISPATCHING' and (
                    developer['provider_type'] != expected_provider or artifact is not None or
                    usage is not None or developer['error_code'] is not None or
                    (token_cap_required and developer['max_tokens'] is None)):
                _fail()
            if dev_state == 'DEVELOPER_UNAVAILABLE' and (
                    artifact is not None or usage is not None or
                    developer['error_code'] is None):
                _fail()
            if dev_state == 'DEVELOPER_OUTCOME_UNKNOWN' and (
                    developer['provider_type'] != expected_provider or artifact is not None or
                    developer['error_code'] is None or
                    (usage is not None and
                     (developer['provider_profile'] != 'codex' or
                      type(developer.get('execution')) is not dict or
                      developer['execution'].get('usage_source') != 'CLI_REPORTED'))):
                _fail()
            if dev_state == 'DEVELOPER_RESPONDED' and (
                    developer['provider_type'] is None or artifact is None or
                    developer['error_code'] is not None or
                    (token_cap_required and developer['max_tokens'] is None)):
                _fail()
            if dev_state == 'BLOCKED' and (
                    developer['provider_profile'] != 'agy' or
                    developer['provider_type'] != 'agy-cli' or
                    developer['error_code'] is None or artifact is not None):
                _fail()
            if developer['provider_profile'] == 'opencode':
                execution = developer.get('execution')
                if dev_state == 'DEVELOPER_RESPONDED' and (
                        type(execution) is not dict or execution['status'] not in ('SUCCEEDED', 'DONE') or
                        execution['usage_source'] != ('CLI_REPORTED' if usage is not None else 'UNKNOWN')):
                    _fail()
                if dev_state == 'DEVELOPER_DISPATCHING' and execution is not None:
                    _fail()
            if dev_state == 'DEVELOPER_OUTPUT_REJECTED' and (
                    developer['provider_type'] is None or artifact is not None or
                    developer['error_code'] not in _DEVELOPER_REJECTION_CODES):
                _fail()
            if 'direct_result_callback' in developer:
                callback = developer['direct_result_callback']
                callback_fields = frozenset((
                    'state', 'executor_task_id', 'evidence', 'missing_gates',
                    'pm_dispatch', 'pm_response', 'pm_recommendation',
                    'pm_usage', 'pm_error_code'))
                states = ('WAITING_EXECUTOR', 'VERIFYING_EXECUTOR', 'WAITING_PM',
                          'PM_EVIDENCE_READY',
                          'PM_DISPATCHING', 'PM_RETURNED', 'PM_RESPONSE_REJECTED',
                          'PM_UNAVAILABLE', 'PM_OUTCOME_UNKNOWN')
                dispatch_for_state = {
                    'WAITING_EXECUTOR': 'NOT_STARTED',
                    'VERIFYING_EXECUTOR': 'NOT_STARTED',
                    'WAITING_PM': 'NOT_STARTED', 'PM_EVIDENCE_READY': 'NOT_STARTED',
                    'PM_DISPATCHING': 'CLAIMED',
                    'PM_RETURNED': 'RETURNED', 'PM_RESPONSE_REJECTED': 'RETURNED',
                    'PM_UNAVAILABLE': 'UNAVAILABLE', 'PM_OUTCOME_UNKNOWN': 'UNKNOWN'}
                if (developer['provider_profile'] not in ('opencode', 'codex', 'agy') or
                        type(callback) is not dict or
                        set(callback) not in (callback_fields, callback_fields | {'review_handoff'},
                            callback_fields | {'review_handoff', 'review_return'}) or
                        callback['state'] not in states or
                        callback['pm_dispatch'] != dispatch_for_state[callback['state']] or
                        not _safe_identifier(callback['executor_task_id'], D6_TASK_ID)):
                    _fail()
                missing = callback['missing_gates']
                if (type(missing) is not list or len(missing) > 32 or
                        any(type(item) is not str or
                            re.fullmatch(r'[A-Z0-9_]{1,64}', item) is None
                            for item in missing) or missing != sorted(set(missing))):
                    _fail()
                evidence = callback['evidence']
                if (type(evidence) is not dict or len(_json_bytes(evidence)) > 64 * 1024 or
                        _emitted_sensitive(_json_bytes(evidence).decode('utf-8')) or
                        evidence.get('task_id') != record['task_id'] or
                        evidence.get('run_id') != record['run_id'] or
                        evidence.get('contract_digest') != record['task_binding']['contract_digest'] or
                        evidence.get('executor_task_id') != callback['executor_task_id'] or
                        evidence.get('source_sha') != developer['source_sha'] or
                        evidence.get('execution_mode') != 'DIRECT_D6' or
                        evidence.get('verification_state') not in
                            ('NOT_STARTED', 'CLAIMED', 'READY', 'FAILED', 'UNKNOWN')):
                    _fail()
                if callback['state'] == 'VERIFYING_EXECUTOR' and (
                        evidence.get('verification_state') != 'CLAIMED' or missing):
                    _fail()
                if callback['state'] in ('WAITING_EXECUTOR',) and (
                        callback['pm_dispatch'] != 'NOT_STARTED' or
                        callback['pm_response'] is not None or
                        callback['pm_recommendation'] is not None):
                    _fail()
                if callback['state'] in ('WAITING_PM', 'PM_EVIDENCE_READY') and (
                        callback['pm_dispatch'] != 'NOT_STARTED' or
                        callback['pm_response'] is not None or
                        callback['pm_recommendation'] is not None or missing):
                    _fail()
                # Evidence ready for a live-runtime PM: only a record-pm Task has
                # no provider profile, and nothing about a PM call is recorded.
                if callback['state'] == 'PM_EVIDENCE_READY' and (
                        'pm_provenance' not in record or
                        callback['pm_usage'] is not None or
                        callback['pm_error_code'] is not None or
                        'review_handoff' in callback):
                    _fail()
                if callback['state'] in ('WAITING_PM', 'PM_EVIDENCE_READY', 'PM_DISPATCHING',
                        'PM_RETURNED', 'PM_RESPONSE_REJECTED', 'PM_UNAVAILABLE',
                        'PM_OUTCOME_UNKNOWN'):
                    result = evidence.get('result')
                    tests = evidence.get('tests')
                    review_ci = evidence.get('review_pr_ci')
                    if (evidence.get('verification_state') != 'READY' or
                            type(result) is not dict or type(tests) is not list or not tests or
                            type(review_ci) is not dict or
                            review_ci != {'stage': 'EXECUTOR_RESULT',
                                'formal_review_state': 'NOT_STARTED',
                                'ci_state': 'NOT_STARTED'} or
                            result.get('result_mode') != 'UNCOMMITTED_WORKTREE' or
                            result.get('result_head') is not None or
                            result.get('task_id') != record['task_id'] or
                            result.get('run_id') != record['run_id'] or
                            result.get('source_sha') != developer['source_sha'] or
                            type(result.get('diff_sha256')) is not str or
                            re.fullmatch(r'[0-9a-f]{64}', result['diff_sha256']) is None or
                            result.get('executor_task_id') != callback['executor_task_id']):
                        _fail()
                    paths = result.get('changed_paths')
                    if (type(paths) is not list or not paths or paths != sorted(set(paths)) or
                            not set(paths).issubset(set(developer['write_paths'])) or
                            any(type(test) is not dict or
                                (test.get('status') == 'NOT_APPLICABLE_DOCS_ONLY' and
                                 (test.get('docs_only') is not True or
                                  test.get('changed_paths') != paths or
                                  test.get('source_sha') != developer['source_sha'] or
                                  test.get('result_head') is not None or
                                  test.get('diff_sha256') != result['diff_sha256'] or
                                  not all(path.endswith('.md') for path in paths))) or
                                (test.get('status') != 'NOT_APPLICABLE_DOCS_ONLY' and
                                (test.get('exit_code') != 0 or
                                type(test.get('exit_code')) is not int or
                                test.get('source_sha') != developer['source_sha'] or
                                test.get('result_head') is not None or
                                test.get('diff_sha256') != result['diff_sha256'] or
                                test.get('path') not in developer['read_paths'] +
                                    developer['write_paths'] or
                                type(test.get('output_sha256')) is not str or
                                re.fullmatch(r'[0-9a-f]{64}', test['output_sha256']) is None))
                                for test in tests)):
                        _fail()
                callback_response = callback['pm_response']
                if callback_response is not None and (
                        type(callback_response) is not str or
                        len(callback_response.encode('utf-8')) > 64 * 1024 or
                        _emitted_sensitive(callback_response)):
                    _fail()
                recommendation = callback['pm_recommendation']
                if recommendation is not None and (
                        type(recommendation) is not dict or set(recommendation) !=
                        {'decision', 'summary'} or recommendation['decision'] not in
                        ('SEND_TO_REVIEW', 'RETURN_TO_DEVELOPER', 'REASSIGN_DEVELOPER',
                         'WAIT_FOR_AUTHORIZATION', 'STOP') or
                        type(recommendation['summary']) is not str or
                        not recommendation['summary'] or
                        len(recommendation['summary'].encode('utf-8')) > 2048 or
                        _emitted_sensitive(recommendation['summary'])):
                    _fail()
                usage = callback['pm_usage']
                if usage is not None:
                    try:
                        _validate_usage(usage)
                    except Exception:
                        _fail()
                if 'review_return' in callback:
                    handoff = callback.get('review_handoff')
                    returned = callback['review_return']
                    try:
                        if type(handoff) is not dict:
                            _fail()
                        from .task_completion import validate_review_return_record
                        validate_review_return_record(returned, handoff)
                    except Exception:
                        _fail()
                    states = {
                        'PENDING': ('NOT_STARTED', None),
                        'DISPATCHING': ('CLAIMED', None),
                        'RETURNED': ('RETURNED', None),
                        'REJECTED': ('RETURNED', 'PM_RESPONSE_REJECTED'),
                        'UNAVAILABLE': ('UNAVAILABLE', returned.get('pm_error_code')),
                        'OUTCOME_UNKNOWN': ('UNKNOWN', returned.get('pm_error_code')),
                    }
                    state = returned['state']
                    if (state not in states or
                            (returned['pm_dispatch'], returned['pm_error_code']) != states[state]):
                        _fail()
                    response = returned['pm_response']
                    recommendation = returned['pm_recommendation']
                    if response is not None and (type(response) is not str or
                            len(response.encode('utf-8')) > 64 * 1024 or
                            _emitted_sensitive(response)):
                        _fail()
                    if recommendation is not None and (
                            type(recommendation) is not dict or set(recommendation) !=
                            {'decision', 'summary'} or recommendation['decision'] not in
                            ('CONTINUE_INTEGRATION_GATES', 'RETURN_TO_DEVELOPER',
                             'WAIT_FOR_REVIEWER', 'STOP') or
                            type(recommendation['summary']) is not str or
                            not recommendation['summary'] or
                            len(recommendation['summary'].encode('utf-8')) > 2048 or
                            _emitted_sensitive(recommendation['summary'])):
                        _fail()
                    usage = returned['pm_usage']
                    if usage is not None:
                        try:
                            _validate_usage(usage)
                        except Exception:
                            _fail()
                    if state == 'RETURNED' and (response is None or recommendation is None):
                        _fail()
                    if state != 'RETURNED' and (response is not None or recommendation is not None or
                                                usage is not None):
                        _fail()
                error = callback['pm_error_code']
                if error is not None and (type(error) is not str or
                        re.fullmatch(r'[A-Z0-9_]{1,64}', error) is None):
                    _fail()
                if 'review_handoff' in callback:
                    result = evidence.get('result') if type(evidence) is dict else None
                    try:
                        if (callback['pm_recommendation'] is None or
                                callback['pm_recommendation']['decision'] != 'SEND_TO_REVIEW' or
                                type(result) is not dict):
                            _fail()
                        from .task_completion import validate_review_handoff_record
                        validate_review_handoff_record(callback['review_handoff'],
                            task_id=record['task_id'], run_id=record['run_id'],
                            contract_digest=record['task_binding']['contract_digest'],
                            developer_task_id=callback['executor_task_id'],
                            developer_identity=evidence.get('executor_identity'),
                            developer_session_ref=evidence.get('executor_session_ref'),
                            source_sha=developer['source_sha'],
                            result_head=result.get('result_head'),
                            diff_sha256=result.get('diff_sha256'),
                            changed_paths=result.get('changed_paths'))
                    except Exception:
                        _fail()
        if 'completion' in record:
            completion = record['completion']
            fields = frozenset(('state', 'executor_task_id', 'reviewer_task_id',
                                'evidence', 'missing_gates', 'pm_dispatch',
                                'pm_response', 'pm_recommendation', 'pm_usage',
                                'pm_error_code'))
            if completion is not None:
                if type(completion) is not dict or set(completion) != fields:
                    _fail()
            if completion is not None and (completion['state'] not in ('WAITING_EXECUTOR',
                    'WAITING_REVIEW', 'WAITING_CI', 'WAITING_PM', 'PM_EVIDENCE_READY',
                    'PM_DISPATCHING', 'PM_RETURNED', 'PM_RESPONSE_REJECTED', 'PM_UNAVAILABLE',
                    'PM_OUTCOME_UNKNOWN') or
                    completion['pm_dispatch'] not in ('NOT_STARTED', 'CLAIMED',
                    'RETURNED', 'UNAVAILABLE', 'UNKNOWN')):
                _fail()
            if completion is not None:
                dispatch_for_state = {
                    'WAITING_EXECUTOR': 'NOT_STARTED',
                    'WAITING_REVIEW': 'NOT_STARTED',
                    'WAITING_CI': 'NOT_STARTED',
                    'WAITING_PM': 'NOT_STARTED',
                    'PM_EVIDENCE_READY': 'NOT_STARTED',
                    'PM_DISPATCHING': 'CLAIMED',
                    'PM_RETURNED': 'RETURNED',
                    'PM_RESPONSE_REJECTED': 'RETURNED',
                    'PM_UNAVAILABLE': 'UNAVAILABLE',
                    'PM_OUTCOME_UNKNOWN': 'UNKNOWN',
                }
                if completion['pm_dispatch'] != dispatch_for_state[completion['state']]:
                    _fail()
                if completion['state'] == 'WAITING_PM' and completion['missing_gates']:
                    _fail()
                # Evidence ready for a live-runtime PM (record-pm): no provider
                # profile exists, so no PM call, reply, usage or error is recorded.
                if completion['state'] == 'PM_EVIDENCE_READY' and (
                        'pm_provenance' not in record or completion['missing_gates'] or
                        completion['pm_response'] is not None or
                        completion['pm_recommendation'] is not None or
                        completion['pm_usage'] is not None or
                        completion['pm_error_code'] is not None):
                    _fail()
                if completion['state'] in ('WAITING_EXECUTOR', 'WAITING_REVIEW', 'WAITING_CI') and (
                        completion['pm_dispatch'] != 'NOT_STARTED' or
                        completion['pm_response'] is not None or
                        completion['pm_recommendation'] is not None):
                    _fail()
            for name in ('executor_task_id', 'reviewer_task_id') if completion is not None else ():
                item = completion[name]
                if item is not None and (type(item) is not str or
                        re.fullmatch(r'[a-z0-9][a-z0-9-]{0,95}', item) is None):
                    _fail()
            if completion is not None and (type(completion['missing_gates']) is not list or
                    len(completion['missing_gates']) > 32 or
                    any(type(item) is not str or
                        re.fullmatch(r'[A-Z0-9_]{1,64}', item) is None
                        for item in completion['missing_gates']) or
                    completion['missing_gates'] != sorted(set(completion['missing_gates']))):
                _fail()
            if completion is None:
                evidence = None
            else:
                evidence = completion['evidence']
            if evidence is not None:
                try:
                    encoded = _json_bytes(evidence)
                except Exception:
                    _fail()
                if len(encoded) > 64 * 1024 or _emitted_sensitive(encoded.decode('utf-8')):
                    _fail()
                if ('execution_mode' in evidence and
                        evidence['execution_mode'] not in ('DIRECT_D6', 'HOST_WORKER')):
                    _fail()
            if completion is not None and completion['state'] in (
                    'WAITING_PM', 'PM_EVIDENCE_READY', 'PM_DISPATCHING', 'PM_RETURNED',
                    'PM_RESPONSE_REJECTED',
                    'PM_UNAVAILABLE', 'PM_OUTCOME_UNKNOWN'):
                if (type(evidence) is not dict or
                        not {'task_id', 'run_id', 'contract_digest', 'result', 'tests',
                             'review_pr_ci'}.issubset(evidence) or
                        evidence['task_id'] != record['task_id'] or
                        evidence['run_id'] != record['run_id'] or
                        evidence['contract_digest'] != record['task_binding']['contract_digest'] or
                        type(evidence['result']) is not dict or
                        type(evidence['tests']) is not list or not evidence['tests'] or
                        type(evidence['review_pr_ci']) is not dict):
                    _fail()
                result_head = evidence['result'].get('result_head')
                review_ci = evidence['review_pr_ci']
                if (type(result_head) is not str or
                        re.fullmatch(r'[0-9a-f]{40}', result_head) is None or
                        evidence['result'].get('task_id') != record['task_id'] or
                        evidence['result'].get('run_id') != record['run_id'] or
                        any(type(test) is not dict or
                            (test.get('status') == 'NOT_APPLICABLE_DOCS_ONLY' and
                             (test.get('docs_only') is not True or
                              test.get('source_sha') != result_head or
                              test.get('changed_paths') != evidence['result'].get('changed_paths') or
                              not all(path.endswith('.md') for path in
                                      evidence['result'].get('changed_paths', [])))) or
                            (test.get('status') != 'NOT_APPLICABLE_DOCS_ONLY' and
                            (test.get('source_sha') != result_head or
                            type(test.get('exit_code')) is not int or test.get('exit_code') != 0
                            ))
                            for test in evidence['tests'])):
                    _fail()
                if review_ci.get('stage') == 'EXECUTOR_RESULT':
                    if (set(review_ci) != {'stage', 'formal_review_state', 'ci_state'} or
                            review_ci.get('formal_review_state') != 'NOT_STARTED' or
                            review_ci.get('ci_state') != 'NOT_STARTED' or
                            completion['reviewer_task_id'] is not None or
                            (completion['pm_recommendation'] is not None and
                             completion['pm_recommendation']['decision'] == 'ACCEPT')):
                        _fail()
                else:
                    checks = review_ci.get('successful_checks')
                    required = review_ci.get('required_checks')
                    if (review_ci.get('formal_review_state') != 'ACCEPTED' or
                            review_ci.get('head_sha') != result_head or
                            review_ci.get('review_commit_sha') != result_head or
                            review_ci.get('ci_head_sha') != result_head or
                            review_ci.get('review_task_id') != record['task_id'] or
                            review_ci.get('review_run_id') != record['run_id'] or
                            review_ci.get('review_contract_digest') != record['task_binding']['contract_digest'] or
                            review_ci.get('review_binding_verified') is not True or
                            review_ci.get('reviewer_task_id') != completion['reviewer_task_id'] or
                            review_ci.get('review_actor') != review_ci.get('reviewer_github_login') or
                            type(review_ci.get('reviewer_d6_identity')) is not str or
                            not review_ci['reviewer_d6_identity'] or
                            review_ci.get('review_session_identity') != 'D6_NATIVE_SESSION_VERIFIED' or
                            type(checks) is not list or type(required) is not list or
                            not required or any(type(item) is not str for item in checks + required) or
                            not set(required).issubset(set(checks))):
                        _fail()
                if (evidence.get('execution_mode') == 'HOST_WORKER' and
                        not validate_host_worker_receipt(
                            evidence.get('host_worker_receipt'),
                            task_binding=record['task_binding'],
                            task_id=record['task_id'], run_id=record['run_id'],
                            contract_digest=record['task_binding']['contract_digest'],
                            source_sha=evidence['result'].get('source_sha'))):
                    _fail()
            completion_response = None if completion is None else completion['pm_response']
            if completion_response is not None and (type(completion_response) is not str or
                    len(completion_response.encode('utf-8')) > 64 * 1024 or
                    _emitted_sensitive(completion_response)):
                _fail()
            completion_recommendation = None if completion is None else completion['pm_recommendation']
            if completion_recommendation is not None and (
                    type(completion_recommendation) is not dict or set(completion_recommendation) !=
                    {'decision', 'summary'} or completion_recommendation['decision'] not in
                    ('ACCEPT', 'SEND_TO_REVIEW', 'RETURN_TO_DEVELOPER', 'REASSIGN_DEVELOPER',
                     'WAIT_FOR_AUTHORIZATION', 'STOP') or
                    type(completion_recommendation['summary']) is not str or
                    not completion_recommendation['summary'] or
                    len(completion_recommendation['summary'].encode('utf-8')) > 2048 or
                    _emitted_sensitive(completion_recommendation['summary'])):
                _fail()
            completion_usage = None if completion is None else completion['pm_usage']
            if completion_usage is not None:
                try:
                    _validate_usage(completion_usage)
                except Exception:
                    _fail()
            error = None if completion is None else completion['pm_error_code']
            if error is not None and (type(error) is not str or
                    re.fullmatch(r'[A-Z0-9_]{1,64}', error) is None):
                _fail()
    if state == 'PM_PENDING' and (request is None or response is not None or
                                  record['error_code'] is not None or record['usage'] is not None):
        _fail()
    if state == 'PM_UNAVAILABLE' and (request is None or response is not None or
                                      record['error_code'] is None or record['usage'] is not None):
        _fail()
    if state == 'INPUT_REJECTED' and (request is not None or response is not None or
                                      record['error_code'] != 'PM_INPUT_REJECTED' or
                                      record['backend_session_semantics'] != 'NOT_STARTED' or
                                      record['pm_provider_type'] is not None or record['usage'] is not None):
        _fail()
    if state == 'PM_RESPONSE_REJECTED' and (request is None or response is not None or
                                            record['error_code'] != 'PM_RESPONSE_REJECTED' or
                                            record['pm_provider_type'] is None or
                                            record['backend_session_semantics'] == 'NOT_STARTED' or
                                            record['usage'] is not None):
        _fail()
    if state == 'PM_RESPONDED' and (request is None or not response or
                                    record['error_code'] is not None or
                                    record['pm_provider_type'] is None or
                                    record['backend_session_semantics'] == 'NOT_STARTED'):
        _fail()
    if type(record['record_sha256']) is not str or record['record_sha256'] != _digest(record):
        _fail()
    return record


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
            info.st_gid, info.st_size, info.st_mtime_ns, info.st_nlink)


class TaskStore:
    """Owner-only, per-task JSON files with stable reads and atomic updates."""

    def __init__(self, root=None):
        self.root = Path(default_root() if root is None else root).absolute()

    def _ensure_root(self):
        if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'):
            _fail('TASK_STORE_UNAVAILABLE')
        current = Path(self.root.anchor)
        uid = os.getuid()
        for part in self.root.parts[1:]:
            current = current / part
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError:
                _fail('TASK_STORE_UNAVAILABLE')
            try:
                info = current.lstat()
            except OSError:
                _fail('TASK_STORE_UNAVAILABLE')
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                _fail('TASK_STORE_UNAVAILABLE')
            if info.st_uid not in (uid, 0):
                _fail('TASK_STORE_UNAVAILABLE')
            if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
                _fail('TASK_STORE_UNAVAILABLE')
        info = self.root.lstat()
        if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
            _fail('TASK_STORE_UNAVAILABLE')

    def _dirfd(self):
        self._ensure_root()
        try:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(fd)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) != 0o700):
                os.close(fd)
                _fail('TASK_STORE_UNAVAILABLE')
            return fd
        except LLMToolError:
            raise
        except OSError:
            _fail('TASK_STORE_UNAVAILABLE')

    @staticmethod
    def _name(task_id):
        if not _safe_identifier(task_id, TASK_ID):
            _fail()
        return task_id + '.json'

    def _read_at(self, directory, task_id):
        name = self._name(task_id)
        for attempt in range(3):
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
                before = os.fstat(fd)
                if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                        stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
                        before.st_size > MAX_RECORD_BYTES):
                    os.close(fd)
                    _fail()
                with os.fdopen(fd, 'rb') as stream:
                    raw = stream.read(MAX_RECORD_BYTES + 1)
                    stream.seek(0)
                    repeated = stream.read(MAX_RECORD_BYTES + 1)
                    after = os.fstat(stream.fileno())
                current = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if (len(raw) > MAX_RECORD_BYTES or raw != repeated or
                        _identity(before) != _identity(after)):
                    _fail()
                if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
                    if attempt < 2:
                        continue
                    _fail('TASK_STORE_BUSY')
                if _identity(before) != _identity(current):
                    _fail()
                value = json.loads(raw.decode('utf-8'), object_pairs_hook=self._unique_pairs,
                                   parse_constant=lambda _: _fail())
                if value.get('task_id') != task_id:
                    _fail()
                return _validate(value)
            except LLMToolError:
                raise
            except FileNotFoundError:
                raise LLMToolError('task not found') from None
            except Exception:
                _fail()

    @staticmethod
    def _unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail()
            result[key] = value
        return result

    @staticmethod
    def _encoded(record):
        value = dict(record)
        value['record_sha256'] = _digest(value)
        _validate(value)
        raw = _json_bytes(value)
        if len(raw) > MAX_RECORD_BYTES:
            _fail()
        return value, raw

    def _write(self, record, *, create):
        record = dict(record)
        record, raw = self._encoded(record)
        name = self._name(record['task_id'])
        directory = self._dirfd()
        temporary = '.' + record['task_id'] + '.' + uuid.uuid4().hex + '.tmp'
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            fd = os.open(temporary, flags, 0o600, dir_fd=directory)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if create:
                os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory,
                        follow_symlinks=False)
                os.unlink(temporary, dir_fd=directory)
            else:
                deadline = time.monotonic() + TASK_UPDATE_LOCK_TIMEOUT_SECONDS
                while True:
                    try:
                        os.replace(temporary, name, src_dir_fd=directory,
                                   dst_dir_fd=directory)
                        break
                    except OSError as exc:
                        if not _is_contention_error(exc):
                            raise
                        if time.monotonic() >= deadline:
                            _fail('TASK_STORE_BUSY')
                        time.sleep(TASK_UPDATE_LOCK_POLL_SECONDS)
            os.fsync(directory)
        except FileExistsError:
            _fail('TASK_STORE_CONFLICT')
        except LLMToolError:
            raise
        except OSError:
            _fail('TASK_STORE_UNAVAILABLE')
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
            os.close(directory)
        return record

    def create(self, record):
        if type(record) is dict and 'abandonment' in record:
            _fail('TASK_ABANDONMENT_MUTATION_FORBIDDEN')
        if (type(record) is not dict or 'dispatch_audit' in record or
                'completion_events' in record or 'completion_event_tombstones' in record):
            _fail()
        record = dict(record)
        developer = record.get('developer')
        if type(developer) is dict:
            from . import worker_dispatch
            gate_record = {'task_id': record['task_id'], 'developer': None,
                           'dispatch_audit': []}
            attempts = worker_dispatch.task_store_attempts(
                gate_record, {'developer': developer})
            if attempts:
                denial = worker_dispatch.authorize_task_store_change(
                    gate_record, {'developer': developer})
                record['dispatch_audit'] = gate_record['dispatch_audit']
                if denial is not None:
                    record['developer'] = None
                    if 'completion' in record:
                        record['completion'] = None
                saved = self._write(record, create=True)
                if denial is not None:
                    raise denial
                return saved
        return self._write(record, create=True)

    def task_ids(self):
        """List stored Task IDs (names only; each record is validated on read)."""
        directory = self._dirfd()
        try:
            names = os.listdir(directory)
        except OSError:
            _fail('TASK_STORE_UNAVAILABLE')
        finally:
            os.close(directory)
        return sorted(name[:-5] for name in names
                      if name.endswith('.json') and _safe_identifier(name[:-5], TASK_ID))

    def get(self, task_id):
        directory = self._dirfd()
        try:
            return dict(self._read_at(directory, task_id))
        finally:
            os.close(directory)

    def update(self, task_id, **changes):
        return self._update(task_id, expected=None, changes=changes)

    def compare_and_update(self, task_id, *, expected, **changes):
        """Update only if selected persisted fields still match under the task lock."""
        if type(expected) is not dict or not expected:
            _fail()
        return self._update(task_id, expected=expected, changes=changes)

    def abandon(self, task_id, *, pm_session_id, reason):
        """Record a PM-only terminal abandonment, idempotent for the same reason."""
        if (not _safe_identifier(pm_session_id, SESSION_ID) or type(reason) is not str or
                not 1 <= len(reason) <= 500 or not reason.strip() or
                not all(char.isprintable() for char in reason) or _emitted_sensitive(reason)):
            _fail('TASK_ABANDONMENT_INVALID')
        for _ in range(5):
            current = self.get(task_id)
            if current.get('pm_session_id') != pm_session_id:
                _fail('TASK_PM_SESSION_DENIED')
            saved = current.get('abandonment')
            if saved is not None:
                if saved['reason'] == reason:
                    return current
                _fail('TASK_ABANDONMENT_CONFLICT')
            stamped = dict(reason=reason, abandoned_at=self._completion_now(),
                           pm_session_id=pm_session_id)
            try:
                return self._update(
                    task_id, expected={'record_sha256': current['record_sha256']},
                    changes={'abandonment': stamped}, trusted_abandonment=True,
                    abandonment_pm_session_id=pm_session_id)
            except TaskStoreConflict:
                continue
        _fail('TASK_STORE_BUSY')

    def append_high_debug(self, task_id, entry):
        for _ in range(5):
            current = self.get(task_id)
            entries = current.get('high_debug', [])
            if len(entries) >= 8:
                _fail('HIGH_DEBUG_LIMIT')
            try:
                return self.compare_and_update(task_id, expected={'high_debug': current.get('high_debug')},
                                               high_debug=entries + [entry])
            except TaskStoreConflict:
                continue
        _fail('HIGH_DEBUG_BUSY')

    @staticmethod
    def _completion_now():
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            '+00:00', 'Z')

    @staticmethod
    def _completion_hex(value, length):
        return type(value) is str and re.fullmatch(
            r'[0-9a-f]{' + str(length) + r'}', value) is not None

    @staticmethod
    def _completion_result(event, created):
        # A persisted UNKNOWN intent is never returned as permission to replay.
        return {'event': json.loads(json.dumps(event)), 'created': created}

    def record_completion_event(self, task_id, *, event_id, run_id, session_id,
                                source, source_sha, sequence, payload_sha256,
                                github_metadata=None):
        """Persist a terminal event before any wake; identical replay is inert.

        The caller verifies the terminal source separately. This storage API
        grants no Worker dispatch or PM wake authority.
        """
        if (not self._completion_hex(event_id, 32) or
                not self._completion_hex(run_id, 32) or
                type(session_id) is not str or
                re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:_-]{0,127}', session_id) is None or
                type(source) is not str or source not in _COMPLETION_SOURCES or
                not self._completion_hex(source_sha, 40) or
                type(sequence) is not int or not 0 <= sequence <= 10**9 or
                not self._completion_hex(payload_sha256, 64)):
            _fail('COMPLETION_EVENT_INVALID')
        binding = dict(event_id=event_id, task_id=task_id, run_id=run_id,
                       session_id=session_id, source=source, source_sha=source_sha,
                       sequence=sequence, payload_sha256=payload_sha256)
        if github_metadata is not None:
            if (source != 'GITHUB' or type(github_metadata) is not dict or
                    set(github_metadata) != _GITHUB_EVENT_FIELDS or
                    type(github_metadata.get('issue_number')) is not int or
                    not 1 <= github_metadata['issue_number'] <= 9999999):
                _fail('COMPLETION_EVENT_INVALID')
            binding.update(github_metadata)
        for _ in range(5):
            current = self.get(task_id)
            if current['schema_version'] != 2 or current['run_id'] != run_id:
                _fail('COMPLETION_EVENT_BINDING_MISMATCH')
            events = current.get('completion_events', [])
            tombstones = current.get('completion_event_tombstones', [])
            for tombstone in tombstones:
                if tombstone['event_id'] == event_id:
                    # Already delivered, acknowledged and compacted: never recreate.
                    if (tombstone['source'] != source or
                            tombstone['session_id'] != session_id):
                        _fail('COMPLETION_EVENT_CONFLICT')
                    return self._completion_result(
                        dict(tombstone, task_id=task_id, state='ACKED', compacted=True),
                        False)
            for existing in events:
                if existing['event_id'] == event_id:
                    if any(existing[key] != value for key, value in binding.items()):
                        _fail('COMPLETION_EVENT_CONFLICT')
                    return self._completion_result(existing, False)
            for existing in events + tombstones:
                if (existing['source'] == source and
                        existing['session_id'] == session_id and
                        existing['sequence'] == sequence):
                    _fail('COMPLETION_EVENT_CONFLICT')
                if (existing['source'] == source and
                        existing['session_id'] == session_id and
                        existing['sequence'] > sequence):
                    _fail('COMPLETION_EVENT_SEQUENCE_INVALID')
            events = list(events)
            tombstones = list(tombstones)
            if len(events) >= MAX_COMPLETION_EVENTS:
                oldest = next((index for index, existing in enumerate(events)
                               if existing['state'] == 'ACKED'), None)
                if oldest is None:
                    # Every stored event is still unacknowledged; nothing may be
                    # dropped. The PM must ack delivered events first.
                    _fail('COMPLETION_EVENT_LIMIT')
                if len(tombstones) >= MAX_COMPLETION_TOMBSTONES:
                    # Dropping dedupe memory could re-deliver an acked event.
                    _fail('COMPLETION_EVENT_HISTORY_LIMIT')
                evicted = events.pop(oldest)
                tombstones.append({key: evicted[key] for key in
                                   _COMPLETION_TOMBSTONE_FIELDS})
            event = dict(binding, pm_session_id=current['pm_session_id'],
                         state='PENDING', received_at=self._completion_now(),
                         consumed_at=None, pm_decision_sha256=None,
                         acked_at=None, dispatch_intent=None)
            changes = {'completion_events': events + [event]}
            if tombstones:
                changes['completion_event_tombstones'] = tombstones
            try:
                self._update(task_id,
                    expected={'record_sha256': current['record_sha256']},
                    changes=changes,
                    trusted_completion_events=True)
                return self._completion_result(event, True)
            except TaskStoreConflict:
                continue
        _fail('TASK_STORE_BUSY')

    def append_remote_test_receipt(self, task_id, receipt):
        """Append one validated remote test receipt idempotently to its Task."""
        if type(receipt) is not dict or set(receipt) not in (
                _REMOTE_TEST_RECEIPT_FIELDS, _REMOTE_TEST_RECEIPT_FIELDS_WITH_EXCERPT):
            _fail('REMOTE_TEST_RECEIPT_INVALID')
        for _ in range(5):
            current = self.get(task_id)
            if (receipt.get('task_id') != task_id or
                    receipt.get('run_id') != current.get('run_id')):
                _fail('REMOTE_TEST_RECEIPT_INVALID')
            receipts = list(current.get('remote_test_receipts', []))
            existing = next((item for item in receipts
                             if item['receipt_digest'] == receipt['receipt_digest']), None)
            if existing is not None:
                if existing != receipt:
                    _fail('REMOTE_TEST_RECEIPT_CONFLICT')
                return existing
            if len(receipts) >= 32:
                _fail('REMOTE_TEST_RECEIPT_LIMIT')
            try:
                self._update(task_id, expected={'record_sha256': current['record_sha256']},
                             changes={'remote_test_receipts': receipts + [receipt]})
                return receipt
            except TaskStoreConflict:
                continue
        _fail('TASK_STORE_BUSY')

    def append_remote_test_outcome(self, task_id, outcome):
        """Persist one bounded no-receipt result; exact replays are idempotent."""
        if type(outcome) is not dict or set(outcome) != _REMOTE_TEST_OUTCOME_FIELDS:
            _fail('REMOTE_TEST_OUTCOME_INVALID')
        for _ in range(5):
            current = self.get(task_id)
            if outcome['result'] not in ('ADMISSION_DENIED', 'TIMEOUT', 'UNAVAILABLE', 'UNKNOWN'):
                _fail('REMOTE_TEST_OUTCOME_INVALID')
            outcomes = list(current.get('remote_test_outcomes', []))
            existing = next((item for item in outcomes if item == outcome), None)
            if existing is not None:
                return {'outcome': existing, 'counter': outcomes.index(existing), 'created': False}
            if len(outcomes) >= MAX_REMOTE_TEST_OUTCOMES:
                _fail('REMOTE_TEST_OUTCOME_LIMIT')
            counter = len(outcomes)
            try:
                self._update(task_id, expected={'record_sha256': current['record_sha256']},
                             changes={'remote_test_outcomes': outcomes + [outcome]})
                return {'outcome': outcome, 'counter': counter, 'created': True}
            except TaskStoreConflict:
                continue
        _fail('TASK_STORE_BUSY')

    def _change_completion_event(self, task_id, event_id, pm_session_id, change):
        if (not self._completion_hex(event_id, 32) or
                not _safe_identifier(pm_session_id, SESSION_ID)):
            _fail('COMPLETION_EVENT_INVALID')
        for _ in range(5):
            current = self.get(task_id)
            if current['pm_session_id'] != pm_session_id:
                _fail('COMPLETION_EVENT_BINDING_MISMATCH')
            events = current.get('completion_events', [])
            position = next((index for index, event in enumerate(events)
                             if event['event_id'] == event_id), None)
            if position is None:
                _fail('COMPLETION_EVENT_NOT_FOUND')
            event = dict(events[position])
            changed = change(event)
            if not changed:
                return self._completion_result(event, False)
            updated = list(events)
            updated[position] = event
            try:
                self._update(task_id,
                    expected={'record_sha256': current['record_sha256']},
                    changes={'completion_events': updated},
                    trusted_completion_events=True)
                return self._completion_result(event, True)
            except TaskStoreConflict:
                continue
        _fail('TASK_STORE_BUSY')

    def consume_completion_event(self, task_id, event_id, *, pm_session_id,
                                 pm_decision_sha256):
        """Persist original-PM consumption before an event may be ACKed."""
        if not self._completion_hex(pm_decision_sha256, 64):
            _fail('COMPLETION_EVENT_INVALID')
        def change(event):
            if event['state'] != 'PENDING':
                if event['pm_decision_sha256'] == pm_decision_sha256:
                    return False
                _fail('COMPLETION_EVENT_CONFLICT')
            event.update(state='PM_CONSUMED', consumed_at=self._completion_now(),
                         pm_decision_sha256=pm_decision_sha256)
            return True
        return self._change_completion_event(task_id, event_id, pm_session_id, change)

    def prepare_completion_dispatch_intent(self, task_id, event_id, *, intent_id,
                                           intent_sha256, pm_session_id):
        """Persist one nonreplayable intent before an external dispatch call."""
        if (not self._completion_hex(intent_id, 32) or
                not self._completion_hex(intent_sha256, 64)):
            _fail('COMPLETION_EVENT_INVALID')
        def change(event):
            if event['state'] != 'PM_CONSUMED':
                _fail('COMPLETION_EVENT_STATE_INVALID')
            prior = event['dispatch_intent']
            if prior is not None:
                if (prior['intent_id'] == intent_id and
                        prior['intent_sha256'] == intent_sha256):
                    return False
                _fail('COMPLETION_EVENT_CONFLICT')
            event['dispatch_intent'] = dict(
                intent_id=intent_id, intent_sha256=intent_sha256,
                state='UNKNOWN', persisted_at=self._completion_now(),
                receipt_sha256=None, confirmed_at=None)
            return True
        return self._change_completion_event(task_id, event_id, pm_session_id, change)

    def confirm_completion_dispatch(self, task_id, event_id, *, intent_id,
                                    receipt_sha256, pm_session_id):
        """Attach a verified receipt; UNKNOWN never authorizes a retry."""
        if (not self._completion_hex(intent_id, 32) or
                not self._completion_hex(receipt_sha256, 64)):
            _fail('COMPLETION_EVENT_INVALID')
        def change(event):
            intent = event['dispatch_intent']
            if intent is None or intent['intent_id'] != intent_id:
                _fail('COMPLETION_EVENT_BINDING_MISMATCH')
            if intent['state'] == 'CONFIRMED':
                if intent['receipt_sha256'] == receipt_sha256:
                    return False
                _fail('COMPLETION_EVENT_CONFLICT')
            event['dispatch_intent'] = dict(intent, state='CONFIRMED',
                                            receipt_sha256=receipt_sha256,
                                            confirmed_at=self._completion_now())
            return True
        return self._change_completion_event(task_id, event_id, pm_session_id, change)

    def ack_completion_event(self, task_id, event_id, *, pm_session_id,
                             pm_decision_sha256):
        """ACK only a durably consumed event with no unresolved dispatch."""
        if not self._completion_hex(pm_decision_sha256, 64):
            _fail('COMPLETION_EVENT_INVALID')
        def change(event):
            if event['pm_decision_sha256'] != pm_decision_sha256:
                _fail('COMPLETION_EVENT_BINDING_MISMATCH')
            if event['state'] == 'ACKED':
                return False
            intent = event['dispatch_intent']
            if (event['state'] != 'PM_CONSUMED' or
                    (intent is not None and intent['state'] != 'CONFIRMED')):
                _fail('COMPLETION_EVENT_STATE_INVALID')
            event.update(state='ACKED', acked_at=self._completion_now())
            return True
        return self._change_completion_event(task_id, event_id, pm_session_id, change)

    def unacked_completion_events(self, task_id):
        """Read pending and uncertain events after process restart."""
        record = self.get(task_id)
        return [json.loads(json.dumps(event))
                for event in record.get('completion_events', [])
                if event['state'] != 'ACKED']

    @staticmethod
    def _worker_timestamp(value, *, now=None, allow_future=False):
        if type(value) is not str:
            _fail('WORKER_AVAILABILITY_INVALID')
        try:
            parsed = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(
                tzinfo=timezone.utc)
        except (TypeError, ValueError):
            _fail('WORKER_AVAILABILITY_INVALID')
        if parsed.strftime('%Y-%m-%dT%H:%M:%SZ') != value:
            _fail('WORKER_AVAILABILITY_INVALID')
        if not allow_future and parsed > (now or datetime.now(timezone.utc)):
            _fail('WORKER_AVAILABILITY_INVALID')
        return parsed

    @classmethod
    def _validate_worker_availability(cls, record, *, now=None):
        if (type(record) is not dict or set(record) != _WORKER_AVAILABILITY_FIELDS or
                type(record.get('schema_version')) is not int or
                record['schema_version'] != WORKER_AVAILABILITY_SCHEMA_VERSION or
                type(record.get('observations')) is not list or
                len(record['observations']) > len(WORKER_KEYS) * len(WORKER_LAYERS)):
            _fail('WORKER_AVAILABILITY_INVALID')
        seen = set()
        for observation in record['observations']:
            if (type(observation) is not dict or
                    set(observation) != _WORKER_OBSERVATION_FIELDS or
                    type(observation.get('worker_key')) is not str or
                    observation['worker_key'] not in WORKER_KEYS or
                    type(observation.get('layer')) is not str or
                    observation['layer'] not in WORKER_LAYERS or
                    type(observation.get('status')) is not str or
                    observation['status'] not in WORKER_OBSERVATION_STATUSES or
                    type(observation.get('source')) is not str or
                    observation['source'] not in WORKER_OBSERVATION_SOURCES or
                    type(observation.get('evidence_sha256')) is not str or
                    re.fullmatch(r'[0-9a-f]{64}', observation['evidence_sha256']) is None):
                _fail('WORKER_AVAILABILITY_INVALID')
            key = (observation['worker_key'], observation['layer'])
            if key in seen:
                _fail('WORKER_AVAILABILITY_INVALID')
            seen.add(key)
            observed_at = cls._worker_timestamp(observation['observed_at'], now=now)
            fresh_until = cls._worker_timestamp(observation['fresh_until'], now=now,
                                                allow_future=True)
            if (fresh_until <= observed_at or
                    fresh_until - observed_at > timedelta(
                        days=WORKER_AVAILABILITY_MAX_AGE_DAYS)):
                _fail('WORKER_AVAILABILITY_INVALID')
        expected_digest = _digest(record)
        if (type(record.get('record_sha256')) is not str or
                record['record_sha256'] != expected_digest):
            _fail('WORKER_AVAILABILITY_INVALID')
        return record

    def _read_worker_availability_at(self, directory):
        try:
            fd = os.open(WORKER_AVAILABILITY_NAME,
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
        except FileNotFoundError:
            return {'schema_version': WORKER_AVAILABILITY_SCHEMA_VERSION,
                    'observations': [], 'record_sha256': None}
        except OSError:
            _fail('WORKER_AVAILABILITY_UNAVAILABLE')
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                    stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
                    before.st_size > WORKER_AVAILABILITY_MAX_BYTES):
                os.close(fd)
                fd = None
                _fail('WORKER_AVAILABILITY_INVALID')
            with os.fdopen(fd, 'rb') as stream:
                fd = None
                raw = stream.read(WORKER_AVAILABILITY_MAX_BYTES + 1)
                after = os.fstat(stream.fileno())
            current = os.stat(WORKER_AVAILABILITY_NAME, dir_fd=directory,
                              follow_symlinks=False)
            if (len(raw) > WORKER_AVAILABILITY_MAX_BYTES or
                    _identity(before) != _identity(after) or
                    _identity(before) != _identity(current)):
                _fail('WORKER_AVAILABILITY_CHANGED')
            record = json.loads(raw.decode('utf-8'),
                                object_pairs_hook=self._unique_pairs,
                                parse_constant=lambda _: _fail(
                                    'WORKER_AVAILABILITY_INVALID'))
            return self._validate_worker_availability(record)
        except LLMToolError:
            raise
        except Exception:
            _fail('WORKER_AVAILABILITY_INVALID')
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _lock_worker_availability(directory, *, exclusive):
        deadline = time.monotonic() + TASK_UPDATE_LOCK_TIMEOUT_SECONDS
        try:
            while True:
                try:
                    lock_fd = os.open(
                        WORKER_AVAILABILITY_LOCK_NAME,
                        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                        0o600, dir_fd=directory)
                    break
                except FileNotFoundError:
                    # macOS can report ENOENT when concurrent openat(O_CREAT)
                    # calls create this lock. Retry only within the same
                    # bounded wait used for TaskStore update locks.
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(TASK_UPDATE_LOCK_POLL_SECONDS)
            info = os.fstat(lock_fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                os.close(lock_fd)
                _fail('WORKER_AVAILABILITY_INVALID')
            fcntl.flock(lock_fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            return lock_fd
        except LLMToolError:
            raise
        except OSError:
            if 'lock_fd' in locals():
                os.close(lock_fd)
            _fail('WORKER_AVAILABILITY_UNAVAILABLE')

    def _write_worker_availability_at(self, directory, record):
        value = dict(record)
        value.pop('record_sha256', None)
        value['record_sha256'] = _digest(value)
        self._validate_worker_availability(value)
        raw = _json_bytes(value)
        if len(raw) > WORKER_AVAILABILITY_MAX_BYTES:
            _fail('WORKER_AVAILABILITY_LIMIT')
        temporary = WORKER_AVAILABILITY_NAME + '.' + uuid.uuid4().hex + '.tmp'
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, WORKER_AVAILABILITY_NAME,
                       src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        except LLMToolError:
            raise
        except OSError:
            _fail('WORKER_AVAILABILITY_UNAVAILABLE')
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
        return value

    @staticmethod
    def _worker_availability_view(record, *, now=None):
        now = now or datetime.now(timezone.utc)
        persisted = {(item['worker_key'], item['layer']): dict(item)
                     for item in record['observations']}
        result = {}
        for worker_key in sorted(WORKER_KEYS):
            result[worker_key] = {}
            for layer in sorted(WORKER_LAYERS):
                item = persisted.get((worker_key, layer))
                if item is None:
                    result[worker_key][layer] = {
                        'status': 'UNKNOWN', 'observed_at': None,
                        'fresh_until': None, 'source': None,
                        'evidence_sha256': None,
                    }
                    continue
                expiry = TaskStore._worker_timestamp(
                    item['fresh_until'], allow_future=True)
                if expiry <= now:
                    item['status'] = 'UNKNOWN'
                del item['worker_key']
                del item['layer']
                result[worker_key][layer] = item
        return result

    def record_worker_observation(self, *, worker_key, layer, status,
                                  observed_at, fresh_until, source,
                                  evidence_sha256):
        """Persist one bounded technical observation without granting authority."""
        candidate = {
            'worker_key': worker_key, 'layer': layer, 'status': status,
            'observed_at': observed_at, 'fresh_until': fresh_until,
            'source': source, 'evidence_sha256': evidence_sha256,
        }
        # Validate the public input before acquiring the shared metadata lock.
        now = datetime.now(timezone.utc)
        self._validate_worker_availability({
            'schema_version': WORKER_AVAILABILITY_SCHEMA_VERSION,
            'observations': [candidate],
            'record_sha256': _digest({
                'schema_version': WORKER_AVAILABILITY_SCHEMA_VERSION,
                'observations': [candidate],
            }),
        }, now=now)
        directory = self._dirfd()
        lock_fd = None
        try:
            lock_fd = self._lock_worker_availability(directory, exclusive=True)
            record = self._read_worker_availability_at(directory)
            observations = [item for item in record['observations']
                            if (item['worker_key'], item['layer']) !=
                            (worker_key, layer)]
            observations.append(candidate)
            observations.sort(key=lambda item: (item['worker_key'], item['layer']))
            saved = self._write_worker_availability_at(directory, {
                'schema_version': WORKER_AVAILABILITY_SCHEMA_VERSION,
                'observations': observations,
            })
            result = self._worker_availability_view(saved)
        finally:
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
            os.close(directory)
        return result

    def get_worker_observations(self):
        """Read a complete worker/layer view; absent or expired results are UNKNOWN."""
        directory = self._dirfd()
        lock_fd = None
        try:
            lock_fd = self._lock_worker_availability(directory, exclusive=False)
            record = self._read_worker_availability_at(directory)
        finally:
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
            os.close(directory)
        return self._worker_availability_view(record)

    def append_dispatch_audit(self, task_id, entry, *, expected=None):
        """Append one bounded gate result using the existing TaskStore CAS lock."""
        if expected is not None and type(expected) is not dict:
            _fail()
        expected_fields = {} if expected is None else {
            key: value for key, value in expected.items() if key != 'dispatch_audit'}
        for _ in range(4):
            current = self.get(task_id)
            audit = list(current.get('dispatch_audit', []))
            if len(audit) >= 64:
                _fail('WORKER_DISPATCH_AUDIT_LIMIT')
            next_audit = audit + [entry]
            # Audit appends compare against the freshest list every time; other
            # caller preconditions (such as the assignment) stay bound.
            preconditions = dict(expected_fields)
            preconditions['dispatch_audit'] = current.get('dispatch_audit')
            try:
                return self._update(task_id, expected=preconditions,
                    changes={'dispatch_audit': next_audit},
                    trusted_dispatch_audit=True)
            except TaskStoreConflict:
                continue
        _fail('TASK_STORE_BUSY')

    def _update(self, task_id, *, expected, changes, trusted_dispatch_audit=False,
                trusted_completion_events=False, trusted_abandonment=False,
                abandonment_pm_session_id=None):
        if 'dispatch_audit' in changes and not trusted_dispatch_audit:
            _fail()
        if (('completion_events' in changes or 'completion_event_tombstones' in changes)
                and not trusted_completion_events):
            _fail('COMPLETION_EVENT_MUTATION_FORBIDDEN')
        if 'abandonment' in changes and not trusted_abandonment:
            _fail('TASK_ABANDONMENT_MUTATION_FORBIDDEN')
        if trusted_abandonment and (set(changes) != {'abandonment'} or
                not _safe_identifier(abandonment_pm_session_id, SESSION_ID)):
            _fail('TASK_ABANDONMENT_MUTATION_FORBIDDEN')
        directory = self._dirfd()
        lock = '.' + self._name(task_id) + '.lock'
        lock_fd = None
        locked = False
        deadline = time.monotonic() + TASK_UPDATE_LOCK_TIMEOUT_SECONDS
        try:
            try:
                while True:
                    try:
                        lock_fd = os.open(
                            lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                            0o600, dir_fd=directory)
                        break
                    except FileNotFoundError:
                        # macOS can fail an openat(O_CREAT) with ENOENT while
                        # another process creates the same name; the next
                        # attempt opens that file. Bounded by the lock wait.
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(TASK_UPDATE_LOCK_POLL_SECONDS)
                    except OSError as exc:
                        if not _is_contention_error(exc):
                            raise
                        if time.monotonic() >= deadline:
                            _fail('TASK_STORE_BUSY')
                        time.sleep(TASK_UPDATE_LOCK_POLL_SECONDS)
                info = os.fstat(lock_fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                        stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                    _fail('TASK_STORE_INVALID')
            except LLMToolError:
                raise
            except OSError as exc:
                # A symlink, directory or other non-file at the lock path is
                # planted state, not an unavailable store.
                code = ('TASK_STORE_INVALID'
                        if exc.errno in (errno.ELOOP, errno.EISDIR, errno.ENXIO, errno.EOPNOTSUPP)
                        else 'TASK_STORE_UNAVAILABLE')
                _fail(code)

            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        _fail('TASK_STORE_BUSY')
                    time.sleep(TASK_UPDATE_LOCK_POLL_SECONDS)
                except OSError as exc:
                    if not _is_contention_error(exc):
                        _fail('TASK_STORE_UNAVAILABLE')
                    if time.monotonic() >= deadline:
                        _fail('TASK_STORE_BUSY')
                    time.sleep(TASK_UPDATE_LOCK_POLL_SECONDS)
            current = self._read_at(directory, task_id)
            if expected is not None and any(current.get(key) != value
                                            for key, value in expected.items()):
                raise TaskStoreConflict() from None
            if trusted_abandonment:
                item = changes['abandonment']
                if (current.get('pm_session_id') != abandonment_pm_session_id or
                        current.get('abandonment') is not None or
                        type(item) is not dict or
                        item.get('pm_session_id') != abandonment_pm_session_id):
                    _fail('TASK_PM_SESSION_DENIED')
            if 'review_dispatch_claim' in changes:
                old_claim = current.get('review_dispatch_claim')
                new_claim = changes['review_dispatch_claim']
                old_claims = ([] if old_claim is None else
                              [old_claim] if type(old_claim) is dict else old_claim)
                new_claims = ([new_claim] if type(new_claim) is dict else new_claim)
                allowed_claim_transition = False
                if type(old_claims) is list and type(new_claims) is list:
                    if len(new_claims) == len(old_claims) + 1 and new_claims[:-1] == old_claims:
                        allowed_claim_transition = (type(new_claims[-1]) is dict and
                                                    new_claims[-1].get('state') == 'CLAIMED')
                    elif len(new_claims) == len(old_claims):
                        changed = [index for index, (before, after) in
                                   enumerate(zip(old_claims, new_claims)) if before != after]
                        if len(changed) == 1:
                            index = changed[0]
                            before, after = old_claims[index], new_claims[index]
                            allowed_claim_transition = (
                                before.get('state') == 'CLAIMED' and
                                after.get('state') in ('UNKNOWN', 'DISPATCHED', 'COMPLETED') and
                                all(before.get(key) == after.get(key) for key in (
                                    'executor_task', 'pr', 'head_sha', 'version',
                                    'account_mode', 'claimed_at', 'reviewer_task')) and
                                (before.get('state') == 'CLAIMED' and
                                 (after.get('state') == 'COMPLETED' or
                                  ('review_result' not in after))) and
                                all(old == new for i, (old, new) in
                                    enumerate(zip(old_claims, new_claims)) if i != index))
                if not allowed_claim_transition:
                    _fail('REVIEW_DISPATCH_CLAIM_TRANSITION_INVALID')
            if 'review_repair_claims' in changes:
                old_claims = current.get('review_repair_claims', [])
                new_claims = changes['review_repair_claims']
                if (type(old_claims) is not list or type(new_claims) is not list or
                        len(new_claims) not in (len(old_claims), len(old_claims) + 1) or
                        len(new_claims) > 64):
                    _fail('REVIEW_REPAIR_CLAIM_TRANSITION_INVALID')
                if len(new_claims) == len(old_claims) + 1:
                    prefix = new_claims[:-1]
                    changed = [i for i, (old, new) in enumerate(zip(old_claims, prefix))
                               if old != new]
                    retry_transition_valid = False
                    if len(changed) == 1:
                        index = changed[0]
                        before, after = old_claims[index], prefix[index]
                        retry_transition_valid = (
                            before.get('state') == 'DISPATCHED' and
                            after.get('state') == 'FAILED' and
                            all(before.get(key) == after.get(key)
                                for key in before.keys() - {'state'}) and
                            all(old == new for i, (old, new) in
                                enumerate(zip(old_claims, prefix)) if i != index))
                    if prefix != old_claims and not retry_transition_valid:
                        _fail('REVIEW_REPAIR_CLAIM_TRANSITION_INVALID')
                    added = new_claims[-1]
                    if (type(added) is not dict or added.get('state') != 'CLAIMED' or
                            type(added.get('family')) is not str or
                            re.fullmatch(r'[a-z0-9._-]{1,64}', added.get('family', '')) is None or
                            type(added.get('repair_task_id')) is not str or
                            re.fullmatch(r'[0-9a-f]{32}', added['repair_task_id']) is None or
                            sum(1 for item in old_claims
                                if item.get('family', f"pr-{item.get('pr')}") ==
                                added.get('family')) >= 3 or
                            added.get('attempt') != 1 + sum(
                                1 for item in old_claims
                                if item.get('family', f"pr-{item.get('pr')}") ==
                                added.get('family'))):
                        _fail('REVIEW_REPAIR_CLAIM_LIMIT_REACHED')
                else:
                    changed = [i for i, (old, new) in enumerate(zip(old_claims, new_claims))
                               if old != new]
                    if len(changed) != 1:
                        _fail('REVIEW_REPAIR_CLAIM_TRANSITION_INVALID')
                    i = changed[0]
                    old, new = old_claims[i], new_claims[i]
                    if (not ((old.get('state') == 'CLAIMED' and
                              new.get('state') in ('DISPATCHED', 'UNKNOWN')) or
                             (old.get('state') == 'DISPATCHED' and
                              new.get('state') == 'FAILED')) or
                            any(old.get(key) != new.get(key) for key in
                                old.keys() - {'state'})):
                        _fail('REVIEW_REPAIR_CLAIM_TRANSITION_INVALID')
            if 'high_debug' in changes:
                old_entries = current.get('high_debug', [])
                new_entries = changes['high_debug']
                if (type(new_entries) is not list or len(new_entries) != len(old_entries) + 1 or
                        new_entries[:-1] != old_entries):
                    _fail('HIGH_DEBUG_IMMUTABLE')
            from . import worker_dispatch
            if trusted_dispatch_audit:
                prior_audit = list(current.get('dispatch_audit', []))
                requested_audit = changes.get('dispatch_audit')
                if (type(requested_audit) is list and
                        len(requested_audit) == len(prior_audit) + 1 and
                        requested_audit[:-1] == prior_audit):
                    attempted = requested_audit[-1]
                    refusal = worker_dispatch._adapter_authorization_block_reason(
                        current, attempted)
                    if refusal is not None:
                        blocked = {**attempted, 'decision': 'BLOCKED',
                                   'reason_code': refusal}
                        current['dispatch_audit'] = prior_audit + [blocked]
                        current['updated_at'] = datetime.now(timezone.utc).replace(
                            microsecond=0).isoformat().replace('+00:00', 'Z')
                        current['record_sha256'] = _digest(current)
                        try:
                            self._write(current, create=False)
                        except Exception:
                            raise worker_dispatch.WorkerDispatchDenied(
                                'WORKER_DISPATCH_AUDIT_UNAVAILABLE') from None
                        raise worker_dispatch.WorkerDispatchDenied(refusal)
            if (type(current.get('developer')) is dict and
                    'developer' in changes and changes['developer'] is None):
                old_developer = current['developer']
                worker_key = worker_dispatch.worker_key_for(
                    old_developer.get('provider_profile'))
                policy = worker_dispatch.evaluate(worker_key or 'unknown')
                denied = worker_dispatch.PolicyDecision(
                    worker_key or 'unknown', False,
                    'WORKER_ASSIGNMENT_CLEAR_FORBIDDEN', policy.policy_sha256)
                audit = list(current.get('dispatch_audit', []))
                if len(audit) >= worker_dispatch.MAX_AUDIT_ENTRIES:
                    raise worker_dispatch.WorkerDispatchDenied(
                        'WORKER_DISPATCH_AUDIT_LIMIT')
                current['dispatch_audit'] = audit + [
                    worker_dispatch.audit_entry(
                        denied, 'REASSIGN',
                        binding=worker_dispatch._assignment_audit_binding(
                            current, old_developer))]
                current['updated_at'] = datetime.now(timezone.utc).replace(
                    microsecond=0).isoformat().replace('+00:00', 'Z')
                current['record_sha256'] = _digest(current)
                try:
                    self._write(current, create=False)
                except Exception:
                    raise worker_dispatch.WorkerDispatchDenied(
                        'WORKER_DISPATCH_AUDIT_UNAVAILABLE') from None
                raise worker_dispatch.WorkerDispatchDenied(
                    'WORKER_ASSIGNMENT_CLEAR_FORBIDDEN')
            dispatch_denial = worker_dispatch.authorize_task_store_change(current, changes, expected)
            if dispatch_denial is not None:
                current['updated_at'] = datetime.now(timezone.utc).replace(
                    microsecond=0).isoformat().replace('+00:00', 'Z')
                current['record_sha256'] = _digest(current)
                self._write(current, create=False)
                raise dispatch_denial
            if set(changes) - (FIELDS - {'schema_version', 'task_id', 'run_id', 'pm_session_id',
                                         'created_at', 'task_contract', 'task_binding', 'record_sha256'}):
                _fail()
            current.update(changes)
            current['updated_at'] = datetime.now(timezone.utc).replace(
                microsecond=0).isoformat().replace('+00:00', 'Z')
            current['record_sha256'] = _digest(current)
            return self._write(current, create=False)
        finally:
            try:
                if locked:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
            finally:
                try:
                    if lock_fd is not None:
                        os.close(lock_fd)
                finally:
                    os.close(directory)
