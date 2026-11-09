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
from .role_config import _NAME
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
    'review_repair_claims', 'high_debug', 'abandonment', 'pm_session_adoptions',
    'pm_provenance', 'merge_attempt', 'published_pr', 'repair_cap_override',
    'publish_operations'))
# One bounded record per publish refusal after this publish's own gh pr create
# (#1139). Values are only int, bool, null or short strings; no command output.
MAX_PUBLISH_OPERATIONS = 8
_PUBLISH_OPERATION_VALUE_BYTES = 512
_PUBLISH_OPERATION_FIELDS = frozenset((
    'task_id', 'repository', 'branch', 'pr', 'creation_url', 'expected_head',
    'observed_head', 'recheck', 'close_attempted', 'record_comment', 'outcome',
    'next_action'))
_REVIEW_DISPATCH_CLAIM_FIELDS = frozenset((
    'executor_task', 'pr', 'head_sha', 'version', 'state', 'reviewer_task',
    'native_session_id', 'account_mode', 'claimed_at', 'updated_at'))
_REVIEW_DISPATCH_CLAIM_ACCOUNT_FIELDS = (
    _REVIEW_DISPATCH_CLAIM_FIELDS | frozenset(('account',)))
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
_REPAIR_CAP_OVERRIDE_VALID_SHAPES = tuple(
    shape | frozenset(('repair_cap_override',)) for shape in (
        *_V2_REPAIR_OPTIONAL_BASES, *_V2_REPAIR_SHAPES, *_HIGH_DEBUG_VALID_SHAPES))
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
_DEVELOPER_REPORTED_FIELDS = frozenset(('tests', 'summary', 'self_review'))
_DEVELOPER_EXECUTOR_TASK_FIELDS = frozenset(('executor_task_id',))
_DEVELOPER_INVALID_FINAL_FIELDS = frozenset(('result_status', 'final_answer'))
_CLAUDE_RESULT_FIELDS = frozenset(('claude_result',))
_DEVELOPER_CALLBACK_FIELDS = _DEVELOPER_FIELDS | frozenset(('direct_result_callback',))
_DEVELOPER_GW_AUDIT_FIELDS = frozenset((
    'audit_sha256', 'audit_copy_path_sha256', 'changed_files'))
_DEVELOPER_SHAPES = (
    _LEGACY_DEVELOPER_FIELDS, _DEVELOPER_FIELDS, _DEVELOPER_CALLBACK_FIELDS,
    _LEGACY_DEVELOPER_FIELDS | frozenset(('owner_assignment_ref',)),
    _DEVELOPER_FIELDS | frozenset(('owner_assignment_ref',)),
    _DEVELOPER_CALLBACK_FIELDS | frozenset(('owner_assignment_ref',)),
    *(_shape | frozenset(('account',)) for _shape in (
        _LEGACY_DEVELOPER_FIELDS, _DEVELOPER_FIELDS, _DEVELOPER_CALLBACK_FIELDS,
        _LEGACY_DEVELOPER_FIELDS | frozenset(('owner_assignment_ref',)),
        _DEVELOPER_FIELDS | frozenset(('owner_assignment_ref',)),
        _DEVELOPER_CALLBACK_FIELDS | frozenset(('owner_assignment_ref',)),
    )),
    *(_shape | _DEVELOPER_GW_AUDIT_FIELDS for _shape in (
        _LEGACY_DEVELOPER_FIELDS, _DEVELOPER_FIELDS, _DEVELOPER_CALLBACK_FIELDS,
        _LEGACY_DEVELOPER_FIELDS | frozenset(('owner_assignment_ref',)),
        _DEVELOPER_FIELDS | frozenset(('owner_assignment_ref',)),
        _DEVELOPER_CALLBACK_FIELDS | frozenset(('owner_assignment_ref',)),
    )),
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
_DEVELOPER_CLAUDE_RESULT_SHAPES = tuple(
    shape | _CLAUDE_RESULT_FIELDS for shape in
    (*_DEVELOPER_BASE_SHAPES, *_DEVELOPER_EXTENSION_SHAPES,
     *_DEVELOPER_BLOCKED_REASON_SHAPES, *_DEVELOPER_PROGRESS_SHAPES)
)
_DEVELOPER_INVALID_FINAL_SHAPES = tuple(
    shape | _DEVELOPER_INVALID_FINAL_FIELDS for shape in (
        *_DEVELOPER_BASE_SHAPES, *_DEVELOPER_EXTENSION_SHAPES,
        *_DEVELOPER_BLOCKED_REASON_SHAPES, *_DEVELOPER_PROGRESS_SHAPES,
        *_DEVELOPER_CLAUDE_RESULT_SHAPES)
)
_DEVELOPER_REPORTED_SHAPES = tuple(
    shape | frozenset(('reported',)) for shape in (
        *_DEVELOPER_BASE_SHAPES, *_DEVELOPER_EXTENSION_SHAPES,
        *_DEVELOPER_BLOCKED_REASON_SHAPES, *_DEVELOPER_PROGRESS_SHAPES,
        *_DEVELOPER_CLAUDE_RESULT_SHAPES, *_DEVELOPER_INVALID_FINAL_SHAPES)
)
_DEVELOPER_ALL_SHAPES = (*_DEVELOPER_BASE_SHAPES, *_DEVELOPER_EXTENSION_SHAPES,
                         *_DEVELOPER_BLOCKED_REASON_SHAPES,
                         *_DEVELOPER_PROGRESS_SHAPES,
                         *_DEVELOPER_CLAUDE_RESULT_SHAPES,
                         *_DEVELOPER_INVALID_FINAL_SHAPES,
                         *_DEVELOPER_REPORTED_SHAPES)
_DEVELOPER_REJECTION_DETAIL_SHAPES = tuple(
    shape | frozenset(('rejection_detail',)) for shape in _DEVELOPER_ALL_SHAPES)
_DIRECT_EXECUTION_FIELDS = frozenset((
    'status', 'native_session_id', 'process_exit_code', 'stdout_sha256',
    'elapsed_ms', 'cleanup_status', 'usage_source', 'source_bytes',
    'source_file_count', 'source_manifest_sha256',
))
_DIRECT_EXECUTION_BINDING_FIELDS = _DIRECT_EXECUTION_FIELDS | frozenset((
    'native_session_binding',
))
_DIRECT_EXECUTION_CHILD_FIELDS = frozenset((
    'child_pid', 'child_start_time', 'child_pgid',
))
_DIRECT_EXECUTION_LAUNCH_FIELDS = _DIRECT_EXECUTION_FIELDS | _DIRECT_EXECUTION_CHILD_FIELDS
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
    """A declared PM session must be a well-formed observed supported session."""
    if not _PM_SESSION_FIELDS.issubset(provenance):
        return provenance['native_session_id_status'] == 'UNAVAILABLE'
    native = provenance['pm_native_session_id']
    cwd = provenance['pm_cwd']
    runtime = provenance['pm_runtime']
    return (runtime in ('claude-code', 'codex') and
            provenance['native_session_id_status'] == 'LOCAL_SESSION_FILE_OBSERVED' and
            type(native) is str and PM_NATIVE_SESSION_ID.fullmatch(native) is not None and
            record['pm_session_id'] == 'pm-' + native and
            type(cwd) is str and 1 <= len(cwd) <= 1024 and cwd.startswith('/') and
            os.path.normpath(cwd) == cwd and
            not any(ord(char) < 0x20 or ord(char) == 0x7f for char in cwd))


# An adoption audit entry: the original three fields, or those plus each side's
# runtime, the new account and the liveness evidence the move relied on (#683).
_PM_ADOPTION_FIELDS = frozenset(('old_session_key', 'new_session_key', 'at'))
_PM_ADOPTION_AUDIT_FIELDS = _PM_ADOPTION_FIELDS | frozenset((
    'old_runtime', 'new_runtime', 'new_account', 'liveness'))
# The STOPPED evidence pm_session_liveness gives the old session under the
# adoption lock, by old runtime: (required codes, optional codes).
_PM_STOPPED_EVIDENCE = {
    'claude-code': (frozenset(('PROCESS_CLEAN', 'LOCK_FREE', 'REGISTRY_CLEAN')),
                    frozenset(('TRANSCRIPT_QUIET',))),
    'codex': (frozenset(('PROCESS_CLEAN', 'LOCK_FREE')), frozenset()),
}


def _valid_pm_adoption_audit(item):
    """The added fields name real runtimes and the old runtime's STOPPED evidence."""
    account = item['new_account']
    liveness = item['liveness']
    old_runtime = item['old_runtime']
    if (type(old_runtime) is not str or old_runtime not in _PM_STOPPED_EVIDENCE or
            type(liveness) is not list or
            not all(type(code) is str for code in liveness) or
            len(liveness) != len(set(liveness))):
        return False
    required, optional = _PM_STOPPED_EVIDENCE[old_runtime]
    return (item['new_runtime'] in ('claude-code', 'codex') and
            (account is None or (type(account) is str and _NAME.fullmatch(account) is not None)) and
            required <= set(liveness) <= required | optional)


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
# An unlocked read reopens a record replaced under it at most this many times.
TASK_READ_ATTEMPTS = 3
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

# #1081 One writer per D6. Every commit that gains a live D6 first appends a
# claim to this bounded journal under the binding lock, so a binder's locked
# check reads only the claims made since its unlocked scan began, never the
# finished Tasks. See TaskStore._d6_commit.
D6_BINDING_LOCK_NAME = '.claude-d6-binding.lock'
D6_BINDING_JOURNAL_NAME = '.d6-binding-journal.json'
D6_BINDING_JOURNAL_SCHEMA_VERSION = 1
D6_BINDING_JOURNAL_MAX_BYTES = 32 * 1024
D6_BINDING_JOURNAL_MAX_ENTRIES = 128
D6_BINDING_ATTEMPTS = 4
_D6_BINDING_JOURNAL_FIELDS = frozenset((
    'schema_version', 'epoch', 'entries', 'record_sha256',
))
_D6_BINDING_ENTRY_FIELDS = frozenset(('epoch', 'd6_sha256', 'task_id'))
# #1133 Claims come only from the binding lock, never from a caller's changes.
_D6_CLAIM_ARGUMENTS = frozenset(('_d6_claims', 'd6_claims'))

# #367 Developer assignment inbox: PM -> Developer ASSIGNED events and their
# one-time claim receipts, in one locked sidecar inside the TaskStore root so
# every account that shares the store sees the same assignments. Each one is
# bound to a registered ACTIVE D6 task and its HEAD (``source_sha``) and
# expires within ASSIGNMENT_EXPIRY_MAX_SECONDS. Only ASSIGNED -> CLAIMED and
# ASSIGNED -> EXPIRED transitions exist.
ASSIGNMENTS_SCHEMA_VERSION = 1
ASSIGNMENTS_NAME = '.developer-assignments.json'
ASSIGNMENTS_LOCK_NAME = '.developer-assignments.lock'
ASSIGNMENTS_MAX_BYTES = 256 * 1024
MAX_ASSIGNMENTS = 128
MAX_ASSIGNMENT_AUDIT = 16
ASSIGNMENT_EXPIRY_MAX_SECONDS = 900
_ASSIGNMENTS_FIELDS = frozenset(('schema_version', 'assignments', 'record_sha256'))
_ASSIGNMENT_FIELDS = frozenset((
    'assignment_id', 'event', 'worker', 'd6_task_id', 'common_dir', 'd6_root',
    'source_sha', 'pm_session_id', 'task_id', 'owner_assignment_ref', 'assigned_at',
    'expires_at', 'dispatch', 'state', 'claim', 'audit',
))
_ASSIGNMENT_DISPATCH_FIELDS = frozenset(('decision', 'reason_code', 'policy_sha256'))
_ASSIGNMENT_CLAIM_FIELDS = frozenset(('session_id', 'claimed_at', 'account_dir_label'))
_ASSIGNMENT_AUDIT_FIELDS = frozenset((
    'action', 'decision', 'reason_code', 'policy_sha256', 'at',
))
_UTC_SECONDS = '%Y-%m-%dT%H:%M:%SZ'


def _valid_utc_seconds(value):
    if type(value) is not str:
        return False
    try:
        return datetime.strptime(value, _UTC_SECONDS).strftime(_UTC_SECONDS) == value
    except ValueError:
        return False


def _valid_github_comment_url_shape(value):
    if type(value) is not str or len(value) > 2048:
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == 'https' and parsed.netloc == 'github.com' and
                parsed.username is None and parsed.password is None and
                parsed.port is None and not parsed.query and
                (re.fullmatch(
                    r'/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}/issues/[1-9][0-9]*/comments/[1-9][0-9]*',
                    parsed.path) is not None and not parsed.fragment or
                 re.fullmatch(
                    r'/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}/issues/[1-9][0-9]*',
                    parsed.path) is not None and
                 re.fullmatch(r'issuecomment-[1-9][0-9]*', parsed.fragment) is not None))
    except ValueError:
        return False


def _utc_seconds(value):
    return datetime.strptime(value, _UTC_SECONDS).replace(tzinfo=timezone.utc)


def _plain_absolute_path(value):
    return (type(value) is str and 1 < len(value) <= 1024 and value.startswith('/') and
            os.path.normpath(value) == value and
            not any(ord(char) < 0x20 or ord(char) == 0x7f for char in value))


def _reason_code(value):
    return type(value) is str and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', value) is not None


def _policy_digest(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _validate_assignment_audit(entry):
    if (type(entry) is not dict or set(entry) != _ASSIGNMENT_AUDIT_FIELDS or
            entry['action'] not in ('ASSIGN', 'CLAIM') or
            entry['decision'] not in ('ALLOWED', 'BLOCKED') or
            not _reason_code(entry['reason_code']) or
            (entry['policy_sha256'] is not None and
             not _policy_digest(entry['policy_sha256'])) or
            not _valid_utc_seconds(entry['at'])):
        _fail('ASSIGNMENTS_INVALID')
    return entry


def _validate_assignment(item):
    if type(item) is not dict or set(item) != _ASSIGNMENT_FIELDS:
        _fail('ASSIGNMENTS_INVALID')
    dispatch = item['dispatch']
    claim = item['claim']
    ref = item['owner_assignment_ref']
    audit = item['audit']
    if (not _safe_identifier(item['assignment_id'], TASK_ID) or
            item['event'] != 'ASSIGNED' or
            type(item['worker']) is not str or
            re.fullmatch(r'[a-z][a-z0-9-]{0,63}', item['worker']) is None or
            not _safe_identifier(item['d6_task_id'], D6_TASK_ID) or
            not _plain_absolute_path(item['common_dir']) or
            not item['common_dir'].endswith('/.git') or
            not _plain_absolute_path(item['d6_root']) or
            type(item['source_sha']) is not str or
            re.fullmatch(r'[0-9a-f]{40}', item['source_sha']) is None or
            not _safe_identifier(item['pm_session_id'], SESSION_ID) or
            (item['task_id'] is not None and not _safe_identifier(item['task_id'], TASK_ID)) or
            (ref is not None and (type(ref) is not str or not 0 < len(ref) <= 512 or
                                  any(ord(char) <= 0x20 or ord(char) >= 0x7f
                                      for char in ref))) or
            not _valid_utc_seconds(item['assigned_at']) or
            not _valid_utc_seconds(item['expires_at']) or
            not 0 < (_utc_seconds(item['expires_at']) -
                     _utc_seconds(item['assigned_at'])).total_seconds()
            <= ASSIGNMENT_EXPIRY_MAX_SECONDS or
            type(dispatch) is not dict or set(dispatch) != _ASSIGNMENT_DISPATCH_FIELDS or
            dispatch['decision'] != 'ALLOWED' or
            not _reason_code(dispatch['reason_code']) or
            not _policy_digest(dispatch['policy_sha256']) or
            item['state'] not in ('ASSIGNED', 'CLAIMED', 'EXPIRED') or
            (item['state'] == 'CLAIMED') != (claim is not None) or
            type(audit) is not list or not 0 < len(audit) <= MAX_ASSIGNMENT_AUDIT):
        _fail('ASSIGNMENTS_INVALID')
    for entry in audit:
        _validate_assignment_audit(entry)
    if claim is not None and (
            type(claim) is not dict or set(claim) != _ASSIGNMENT_CLAIM_FIELDS or
            not _safe_identifier(claim['session_id'], PM_NATIVE_SESSION_ID) or
            type(claim['account_dir_label']) is not str or
            re.fullmatch(r'[0-9a-f]{16}', claim['account_dir_label']) is None or
            not _valid_utc_seconds(claim['claimed_at']) or
            _utc_seconds(claim['claimed_at']) >= _utc_seconds(item['expires_at'])):
        _fail('ASSIGNMENTS_INVALID')
    return item


def _validate_assignments(record):
    if (type(record) is not dict or set(record) != _ASSIGNMENTS_FIELDS or
            type(record['schema_version']) is not int or
            record['schema_version'] != ASSIGNMENTS_SCHEMA_VERSION or
            type(record['assignments']) is not list or
            len(record['assignments']) > MAX_ASSIGNMENTS):
        _fail('ASSIGNMENTS_INVALID')
    seen = set()
    for item in record['assignments']:
        _validate_assignment(item)
        if item['assignment_id'] in seen:
            _fail('ASSIGNMENTS_INVALID')
        seen.add(item['assignment_id'])
    if record['record_sha256'] != _digest(record):
        _fail('ASSIGNMENTS_INVALID')
    return record


def _fail(code='TASK_STORE_INVALID', *, holder_task_id=None):
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
    message = 'task store unavailable; details omitted'
    if safe_code == 'TASK_D6_ALREADY_BOUND' and _safe_identifier(holder_task_id, TASK_ID):
        message = f'{safe_code}: D6 is held by Task {holder_task_id}'
    error = LLMToolError(message)
    error.task_store_code = category
    error.task_store_error_code = safe_code
    raise error from None


def _valid_reported_text(value, limit):
    if type(value) is not str or not value.strip():
        return False
    try:
        if len(value.encode('utf-8')) > limit:
            return False
    except UnicodeError:
        return False
    return (not any((ord(char) < 32 and char not in '\t\n\r') or
                    127 <= ord(char) <= 159 or
                    unicodedata.category(char) == 'Cf' for char in value) and
            not _emitted_sensitive(value))


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


class _TaskNotFound(LLMToolError):
    """No record exists for this Task ID; any other read failure is not this."""


class _D6ClaimRequired(LLMToolError):
    """A commit would gain a live D6 that the binding lock has not cleared."""

    def __init__(self, claims):
        super().__init__('D6 claim needs the binding lock')
        self.claims = frozenset(claims)


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


def _d6_digest(executor_task_id):
    return hashlib.sha256(executor_task_id.encode('utf-8')).hexdigest()


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


def _verified_earlier_pm_sessions(record):
    """Earlier PM session keys of a verified adoption chain, else an empty set.

    Verified: a non-empty list whose entries each continue from the previous
    entry's new session, the last ending at the record's current session.
    """
    adoptions = record.get('pm_session_adoptions')
    if (type(adoptions) is not list or not adoptions or
            any(type(item) is not dict or
                type(item.get('old_session_key')) is not str or
                type(item.get('new_session_key')) is not str for item in adoptions)):
        return frozenset()
    for previous, item in zip(adoptions, adoptions[1:]):
        if item['old_session_key'] != previous['new_session_key']:
            return frozenset()
    if adoptions[-1]['new_session_key'] != record.get('pm_session_id'):
        return frozenset()
    return frozenset(item['old_session_key'] for item in adoptions)


def _validate_remote_test_receipts(record):
    receipts = record.get('remote_test_receipts', [])
    if type(receipts) is not list or len(receipts) > 32:
        _fail('REMOTE_TEST_RECEIPT_INVALID')
    # A receipt stays bound to the PM session that ran the test; after adoption
    # that session is accepted only through the record's verified chain.
    earlier_sessions = _verified_earlier_pm_sessions(record)
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
                (receipt.get('pm_session') != record.get('pm_session_id') and
                 (type(receipt.get('pm_session')) is not str or
                  receipt['pm_session'] not in earlier_sessions)) or
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


def _valid_publish_operation(item):
    """Return whether item is one publish operation record of the fixed fields."""
    if type(item) is not dict or set(item) != _PUBLISH_OPERATION_FIELDS:
        return False
    for value in item.values():
        if value is None or type(value) in (int, bool):
            continue
        if type(value) is not str:
            return False
        if len(value.encode('utf-8', 'surrogatepass')) > _PUBLISH_OPERATION_VALUE_BYTES:
            return False
    return True


def _validate_publish_operations(record):
    operations = record.get('publish_operations')
    if (type(operations) is not list or len(operations) > MAX_PUBLISH_OPERATIONS or
            any(not _valid_publish_operation(item) for item in operations)):
        _fail('TASK_PUBLISH_OPERATION_INVALID')


def _known_review_failure_reason(value):
    """Return whether ``value`` is one fixed unusable-review reason name."""
    # Imported here, like worker_dispatch: the store must not load the
    # dispatcher at import time. The dispatcher owns the one fixed set.
    from .review_dispatch import REVIEW_FAILURE_REASONS
    return type(value) is str and value in REVIEW_FAILURE_REASONS


_BINDING_MISMATCH_KEY_NAMES = frozenset(('task_id', 'run_id', 'head'))


def _valid_binding_mismatch_keys(item):
    """Return whether a review claim's binding_mismatch_keys field is allowed.

    Only an UNKNOWN claim refused with VERDICT_BINDING_MISMATCH may carry it,
    as a non-empty list of distinct JSON key names. It never holds a value.
    """
    keys = item.get('binding_mismatch_keys')
    return (item.get('state') == 'UNKNOWN' and
            item.get('failure_reason') == 'VERDICT_BINDING_MISMATCH' and
            type(keys) is list and bool(keys) and
            all(type(key) is str and key in _BINDING_MISMATCH_KEY_NAMES
                for key in keys) and
            len(set(keys)) == len(keys))


def _validate(record):
    keys = set(record) - {'abandonment'} if type(record) is dict else set()
    merge_attempt = record.get('merge_attempt') if type(record) is dict else None
    published_pr = record.get('published_pr') if type(record) is dict else None
    keys.discard('merge_attempt')
    keys.discard('published_pr')
    keys.discard('publish_operations')
    if type(record) is dict and 'pm_session_adoptions' in record:
        keys.discard('pm_session_adoptions')
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
             *_V2_REPAIR_SHAPES, *_HIGH_DEBUG_VALID_SHAPES,
             *_REPAIR_CAP_OVERRIDE_VALID_SHAPES)) or
            record.get('schema_version') not in (1, 2)):
        _fail()
    if merge_attempt is not None:
        if (type(merge_attempt) is not dict or
                set(merge_attempt) != frozenset(('pr', 'head_sha', 'state', 'claimed_at')) or
                type(merge_attempt.get('pr')) is not int or
                not 1 <= merge_attempt['pr'] <= 9999999 or
                type(merge_attempt.get('head_sha')) is not str or
                re.fullmatch(r'[0-9a-f]{40}', merge_attempt['head_sha']) is None or
                merge_attempt.get('state') not in ('CLAIMED', 'UNKNOWN', 'MERGED') or
                type(merge_attempt.get('claimed_at')) is not str):
            _fail('TASK_MERGE_ATTEMPT_INVALID')
    if published_pr is not None:
        if (type(published_pr) is not dict or
                set(published_pr) != frozenset(('pr', 'head_sha', 'recorded_at')) or
                type(published_pr.get('pr')) is not int or
                not 1 <= published_pr['pr'] <= 9999999 or
                type(published_pr.get('head_sha')) is not str or
                re.fullmatch(r'[0-9a-f]{40}', published_pr['head_sha']) is None or
                not _valid_utc_seconds(published_pr.get('recorded_at'))):
            _fail('TASK_PUBLISHED_PR_INVALID')
    if 'publish_operations' in record:
        _validate_publish_operations(record)
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
    if 'pm_session_adoptions' in record:
        adoptions = record['pm_session_adoptions']
        if type(adoptions) is not list or len(adoptions) > 32:
            _fail('PM_SESSION_ADOPTION_INVALID')
        for item in adoptions:
            if (type(item) is not dict or
                    set(item) not in (_PM_ADOPTION_FIELDS, _PM_ADOPTION_AUDIT_FIELDS) or
                    not _safe_identifier(item.get('old_session_key'), SESSION_ID) or
                    not _safe_identifier(item.get('new_session_key'), SESSION_ID) or
                    (set(item) == _PM_ADOPTION_AUDIT_FIELDS and
                     not _valid_pm_adoption_audit(item))):
                _fail('PM_SESSION_ADOPTION_INVALID')
            try:
                stamp = datetime.strptime(item.get('at'), '%Y-%m-%dT%H:%M:%SZ')
            except (TypeError, ValueError):
                _fail('PM_SESSION_ADOPTION_INVALID')
            if stamp.strftime('%Y-%m-%dT%H:%M:%SZ') != item['at']:
                _fail('PM_SESSION_ADOPTION_INVALID')
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
                item.get('status') not in ('RCA_CONFIRMED', 'RCA_UNCONFIRMED', 'BLOCKED', 'REPORT_INVALID',
                                        'RUN_FAILED')):
                _fail('HIGH_DEBUG_INVALID')
    if 'repair_cap_override' in record:
        item = record['repair_cap_override']
        if (type(item) is not dict or set(item) !=
                frozenset(('ref', 'rounds', 'recorded_at')) or
                not _valid_github_comment_url_shape(item.get('ref')) or
                type(item.get('rounds')) is not int or item['rounds'] < 3 or
                not _valid_utc_seconds(item.get('recorded_at'))):
            _fail('TASK_REPAIR_CAP_OVERRIDE_INVALID')
    if 'review_dispatch_claim' in record:
        claim = record['review_dispatch_claim']
        claims = [claim] if type(claim) is dict else claim
        if type(claims) is not list or not claims:
            _fail('REVIEW_DISPATCH_CLAIM_INVALID')
        identities = set()
        for item in claims:
            if (type(item) is not dict or
                    set(item) - frozenset(('failure_reason', 'binding_mismatch_keys')) not in (
                        _REVIEW_DISPATCH_CLAIM_FIELDS,
                        _REVIEW_DISPATCH_CLAIM_FIELDS | frozenset(('review_result',)),
                        _REVIEW_DISPATCH_CLAIM_ACCOUNT_FIELDS,
                        _REVIEW_DISPATCH_CLAIM_ACCOUNT_FIELDS |
                        frozenset(('review_result',))) or
                    ('failure_reason' in item and
                     not _known_review_failure_reason(item['failure_reason'])) or
                    ('binding_mismatch_keys' in item and
                     not _valid_binding_mismatch_keys(item)) or
                    ('account' in item and
                     (type(item['account']) is not str or
                      _NAME.fullmatch(item['account']) is None)) or
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
                                                      'CODEX_CLI', 'ROLES',
                                                      'EXPLICIT', 'LEGACY',
                                                      'BUILTIN_DEFAULT') or
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
                    assignment['route'] not in ('company-gw', 'gw-agent', 'codex', 'opencode', 'agy', 'claude', 'none') or
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
                    assignment.get('route') not in ('company-gw', 'gw-agent', 'opencode', 'codex', 'agy', 'claude') or
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
                                 *_DEVELOPER_PROGRESS_SHAPES,
                                 *_DEVELOPER_CLAUDE_RESULT_SHAPES,
                                 *_DEVELOPER_INVALID_FINAL_SHAPES,
                                 *_DEVELOPER_REPORTED_SHAPES,
                                 *_DEVELOPER_REJECTION_DETAIL_SHAPES)
            if type(developer) is not dict or set(developer) not in assignment_shapes:
                _fail()
            if ('account' in developer and
                    (type(developer['account']) is not str or
                     _NAME.fullmatch(developer['account']) is None)):
                _fail()
            if ('executor_task_id' in developer and
                    not _safe_identifier(developer['executor_task_id'], D6_TASK_ID)):
                _fail()
            if 'reported' in developer:
                reported = developer['reported']
                if (type(reported) is not dict or not reported or
                        not set(reported).issubset(_DEVELOPER_REPORTED_FIELDS)):
                    _fail()
                if 'summary' in reported and (
                        not _valid_reported_text(reported['summary'], 8 * 1024)):
                    _fail()
                if 'self_review' in reported and (
                        not _valid_reported_text(reported['self_review'], 8 * 1024)):
                    _fail()
                if 'tests' in reported:
                    tests = reported['tests']
                    if (type(tests) is not list or len(tests) > 32 or
                            any(not _valid_reported_text(item, 1024) for item in tests)):
                        _fail()
                try:
                    reported_size = len(json.dumps(
                        reported, ensure_ascii=True, sort_keys=True,
                        separators=(',', ':')).encode('ascii'))
                except (TypeError, UnicodeError):
                    _fail()
                if reported_size > 8 * 1024:
                    _fail()
            if (not _safe_identifier(developer['assignment_id'], TASK_ID) or
                    developer['selection_source'] not in ('PM', 'USER') or
                    developer['state'] not in ('DEVELOPER_PENDING', 'DEVELOPER_DISPATCHING',
                                               'DEVELOPER_RESPONDED',
                                               'DEVELOPER_UNAVAILABLE', 'DEVELOPER_OUTCOME_UNKNOWN',
                                               'DEVELOPER_OUTPUT_REJECTED', 'BLOCKED') or
                    developer['provider_profile'] not in ('company-gw', 'codex', 'opencode', 'claude', 'agy', 'gw-agent') or
                    developer['provider_type'] not in (
                        None, 'openai-compatible', 'codex-cli', 'opencode-cli', 'agy-cli', 'gw-agent-cli', 'claude-cli') or
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
                from .worker_dispatch import owner_comment_ref
                ref = developer['owner_assignment_ref']
                # #785 M18: AGY dispatch is decided by the live gate, so an AGY
                # record may carry None; any non-None ref must still be valid.
                agy_without_ref = developer['provider_profile'] == 'agy' and ref is None
                try:
                    valid_owner_ref = agy_without_ref or (
                        type(ref) is str and owner_comment_ref().fullmatch(ref) is not None)
                except LLMToolError:
                    _fail('TASK_STORE_REPOSITORY_CONFIG_INVALID')
                if developer['provider_profile'] not in ('claude', 'agy') or not valid_owner_ref:
                    _fail()
            if developer['provider_profile'] == 'agy' and 'owner_assignment_ref' not in developer:
                _fail()
            if 'audit_sha256' in developer and (
                    developer['provider_profile'] != 'gw-agent' or
                    type(developer.get('audit_sha256')) is not str or
                    re.fullmatch(r'[0-9a-f]{64}', developer['audit_sha256']) is None or
                    type(developer.get('audit_copy_path_sha256')) is not str or
                    re.fullmatch(r'[0-9a-f]{64}', developer['audit_copy_path_sha256']) is None):
                _fail()
            if 'changed_files' in developer and (
                    developer['provider_profile'] != 'gw-agent' or
                    type(developer['changed_files']) is not list or
                    len(developer['changed_files']) > 200 or
                    any(type(path) is not str or not path or len(path) > 512 or
                        path.startswith('/') or '\\' in path or
                        any(part in ('', '.', '..') for part in path.split('/'))
                        for path in developer['changed_files'])):
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
            if 'rejection_detail' in developer:
                detail = developer['rejection_detail']
                if (developer['state'] != 'DEVELOPER_OUTPUT_REJECTED' or
                        type(detail) is not dict or set(detail) != {'reason', 'hunk'} or
                        detail['reason'] not in (
                            'SOURCE_UNREADABLE', 'SECTION_HEADER_INVALID',
                            'HUNK_HEADER_INVALID', 'HUNK_LINE_UNPREFIXED',
                            'OLD_SIDE_NOT_FOUND', 'OLD_SIDE_AMBIGUOUS',
                            'HUNKS_OVERLAP', 'NO_CHANGE', 'STRICT_PARSE',
                            'APPLY_CHECK_FAILED') or
                        (detail['hunk'] is not None and
                         (type(detail['hunk']) is not int or detail['hunk'] < 0))):
                    _fail()
            if 'rejected_output_capture' in developer:
                capture = developer['rejected_output_capture']
                invalid_final_capture = (
                    developer.get('result_status') == 'UNVERIFIED' and
                    developer.get('final_answer') == 'INVALID')
                if ((developer['state'] != 'DEVELOPER_OUTPUT_REJECTED' and
                     not invalid_final_capture) or
                        type(capture) is not dict or set(capture) != {'ref', 'sha256'} or
                        type(capture['ref']) is not str or
                        re.fullmatch(r'rejected-developer-output/[0-9a-f]{32}-[0-9a-f]{16}\.txt',
                                     capture['ref']) is None or
                        type(capture['sha256']) is not str or
                        re.fullmatch(r'[0-9a-f]{64}', capture['sha256']) is None):
                    _fail()
            if 'result_status' in developer or 'final_answer' in developer:
                execution = developer.get('execution')
                if (developer['provider_profile'] != 'codex' or
                        developer['state'] != 'DEVELOPER_RESPONDED' or
                        developer.get('result_status') != 'UNVERIFIED' or
                        developer.get('final_answer') != 'INVALID' or
                        developer.get('error_code') is not None or
                        'rejected_output_capture' not in developer or
                        type(execution) is not dict or execution.get('status') != 'DONE' or
                        execution.get('process_exit_code') != 0 or
                        execution.get('cleanup_status') != 'CONFIRMED'):
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
            if (developer['provider_profile'] == 'gw-agent' and
                    (developer['provider_type'] not in (None, 'gw-agent-cli') or
                     developer['backend_session_semantics'] != 'CLI_REPORTED_SESSION_ID' or
                     developer['max_tokens'] is not None)):
                _fail()
            if developer['provider_profile'] == 'claude':
                semantics = developer['backend_session_semantics']
                record_only = semantics == 'NO_NATIVE_SESSION_ID_EXPOSED'
                cli_route = semantics == 'CLI_REPORTED_SESSION_ID'
                if ('owner_assignment_ref' not in developer or
                        (record_only and (developer['provider_type'] is not None or
                         developer['state'] not in ('DEVELOPER_PENDING', 'DEVELOPER_RESPONDED') or
                         (developer['state'] == 'DEVELOPER_PENDING' and 'claude_result' in developer) or
                         (developer['state'] == 'DEVELOPER_RESPONDED' and
                          ('claude_result' not in developer or developer['artifact'] is not None or
                           developer['usage'] is not None or developer['error_code'] is not None or
                           developer['model'] is not None)))) or
                        (cli_route and (developer['provider_type'] not in (None, 'claude-cli') or
                         developer['max_tokens'] is not None or 'executor_task_id' not in developer or
                         'claude_result' in developer or
                         type(developer.get('direct_result_callback')) is not dict or
                         developer['direct_result_callback'].get('executor_task_id') !=
                         developer.get('executor_task_id')))):
                    _fail()
            if 'claude_result' in developer:
                result = developer['claude_result']
                if (developer['provider_profile'] != 'claude' or
                        developer.get('backend_session_semantics') != 'NO_NATIVE_SESSION_ID_EXPOSED' or
                        type(result) is not dict or
                        set(result) != {'executor_task_id', 'head_sha', 'recorded_at'} or
                        not _safe_identifier(result['executor_task_id'], D6_TASK_ID) or
                        type(result['head_sha']) is not str or
                        re.fullmatch(r'[0-9a-f]{40}', result['head_sha']) is None or
                        type(result['recorded_at']) is not str or
                        re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',
                                     result['recorded_at']) is None):
                    _fail()
            if 'execution' in developer:
                execution = developer['execution']
                if execution is not None:
                    if (developer['provider_profile'] not in (
                            'opencode', 'codex', 'agy', 'claude', 'gw-agent') or
                            type(execution) is not dict or
                            set(execution) not in (
                                _DIRECT_EXECUTION_FIELDS, _DIRECT_EXECUTION_BINDING_FIELDS,
                                _DIRECT_EXECUTION_LAUNCH_FIELDS,
                                *_DEVELOPER_EXECUTION_DIFF_SHAPES) or
                            (set(execution) == _DIRECT_EXECUTION_LAUNCH_FIELDS and
                             (developer['provider_profile'] not in ('codex', 'opencode') or
                              execution['status'] != 'RUNNING' or
                              type(execution['child_pid']) is not int or
                              execution['child_pid'] <= 0 or
                              type(execution['child_pgid']) is not int or
                              execution['child_pgid'] <= 0 or
                              type(execution['child_start_time']) is not str or
                              re.fullmatch(r'[A-Za-z0-9 :_-]{1,64}',
                                           execution['child_start_time']) is None)) or
                            ('native_session_binding' in execution and
                             (developer['provider_profile'] != 'codex' or
                              type(execution['native_session_binding']) is not str or
                              re.fullmatch(r'[A-Z0-9_]{1,64}',
                                           execution['native_session_binding']) is None)) or
                            execution['status'] not in (
                                ('SUCCEEDED', 'FAILED', 'UNKNOWN', 'RUNNING')
                                if developer['provider_profile'] == 'opencode' else
                                ('DONE', 'FAILED', 'UNKNOWN', 'BLOCKED', 'TIMEOUT',
                                 'RUNNING')) or
                            ((execution['status'] == 'RUNNING') !=
                             (set(execution) == _DIRECT_EXECUTION_LAUNCH_FIELDS)) or
                            (execution['status'] == 'RUNNING' and
                             developer['state'] != 'DEVELOPER_DISPATCHING') or
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
            if developer['provider_profile'] == 'gw-agent':
                if (dev_state == 'DEVELOPER_RESPONDED' and
                        (type(model) is not str or _SERVED_MODEL.fullmatch(model) is None)):
                    _fail()
                if (type(usage) is dict and 'served_model' in usage and
                        usage['served_model'] != model):
                    _fail()
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
                claude_isolation_block = (
                    developer['provider_profile'] == 'claude' and
                    developer['state'] == 'BLOCKED' and
                    developer['error_code'] == 'BLOCKED_WRITE_ISOLATION' and
                    reason in ('BLOCKED_WRITE_ISOLATION', 'UNSUPPORTED_BOUNDARY',
                               'BOUNDARY_PREPARATION_FAILED', 'BOUNDARY_UNVERIFIED'))
                claude_dispatch_block = (
                    developer['provider_profile'] == 'claude' and
                    developer['state'] == 'BLOCKED' and
                    developer['error_code'] == 'CLAUDE_DISPATCH_DENIED' and
                    reason == 'OWNER_DISPATCH_DENIED')
                codex_block = (
                    developer['provider_profile'] == 'codex' and
                    developer['state'] == 'DEVELOPER_OUTCOME_UNKNOWN' and
                    developer['error_code'] == 'CODEX_BLOCKED' and
                    type(execution) is dict and execution.get('status') == 'BLOCKED')
                if (not (agy_isolation_block or agy_dispatch_block or claude_isolation_block or
                         claude_dispatch_block or codex_block) or
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
                'gw-agent': 'gw-agent-cli',
                'claude': ('claude-cli' if developer.get('backend_session_semantics') ==
                           'CLI_REPORTED_SESSION_ID' else None),
            }.get(developer['provider_profile'])
            token_cap_required = not (
                developer['provider_profile'] in ('codex', 'agy', 'gw-agent', 'claude') and
                developer['provider_type'] in ('codex-cli', 'agy-cli', 'gw-agent-cli', 'claude-cli'))
            if dev_state == 'DEVELOPER_PENDING' and (
                    developer['provider_type'] is not None or developer['model'] is not None or
                    artifact is not None or usage is not None or developer['error_code'] is not None):
                _fail()
            if 'progress' in developer:
                progress = developer['progress']
                if (developer['provider_profile'] not in ('agy', 'claude') or
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
            claude_record_only = (developer['provider_profile'] == 'claude' and
                                  developer.get('backend_session_semantics') ==
                                  'NO_NATIVE_SESSION_ID_EXPOSED')
            if (dev_state == 'DEVELOPER_RESPONDED' and not claude_record_only and (
                    developer['provider_type'] is None or artifact is None or
                    developer['error_code'] is not None or
                    (token_cap_required and developer['max_tokens'] is None))):
                _fail()
            if dev_state == 'BLOCKED' and (
                    developer['provider_profile'] not in ('agy', 'claude') or
                    developer['provider_type'] != expected_provider or
                    developer['error_code'] is None or artifact is not None):
                _fail()
            if developer['provider_profile'] == 'opencode':
                execution = developer.get('execution')
                if dev_state == 'DEVELOPER_RESPONDED' and (
                        type(execution) is not dict or execution['status'] not in ('SUCCEEDED', 'DONE') or
                        execution['usage_source'] != ('CLI_REPORTED' if usage is not None else 'UNKNOWN')):
                    _fail()
                if (dev_state == 'DEVELOPER_DISPATCHING' and execution is not None and
                        not (type(execution) is dict and execution.get('status') == 'RUNNING' and
                             set(execution) == _DIRECT_EXECUTION_LAUNCH_FIELDS)):
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
                if (developer['provider_profile'] not in ('opencode', 'codex', 'agy', 'gw-agent', 'claude') or
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

    def _read_at(self, directory, task_id, *, validate=True):
        """Read one record with every file check and a strict JSON parse.

        validate=False skips only the final _validate; it is for read-only
        scans whose matches the caller then reads again through get().
        """
        name = self._name(task_id)
        for _ in range(TASK_READ_ATTEMPTS):
            fd = None
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
                before = os.fstat(fd)
                if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                        stat.S_IMODE(before.st_mode) != 0o600 or
                        before.st_nlink not in (0, 1) or
                        before.st_size > MAX_RECORD_BYTES):
                    os.close(fd)
                    fd = None
                    _fail()
                if before.st_nlink == 0:
                    # A concurrent replace unlinked the inode between open and
                    # fstat; it is no longer the record. Reopen (finally closes fd).
                    continue
                with os.fdopen(fd, 'rb') as stream:
                    fd = None
                    raw = stream.read(MAX_RECORD_BYTES + 1)
                    stream.seek(0)
                    repeated = stream.read(MAX_RECORD_BYTES + 1)
                    after = os.fstat(stream.fileno())
                    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if (len(raw) > MAX_RECORD_BYTES or raw != repeated or
                        _identity(before)[:-1] != _identity(after)[:-1] or
                        after.st_nlink not in (0, 1)):
                    _fail()
                if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
                    continue
                if (current.st_nlink == 0 and
                        _identity(before)[:-1] == _identity(current)[:-1]):
                    # stat() resolved the name to the opened inode, then a
                    # concurrent replace dropped its last link before stat()
                    # read its attributes. The entry now names another inode;
                    # reopen rather than return or reject this unlinked one.
                    continue
                # Opened fd and current directory entry are the same inode; it
                # must still be a single-link live entry with identical metadata.
                if (_identity(before) != _identity(after) or
                        _identity(before) != _identity(current) or
                        current.st_nlink != 1):
                    _fail()
                value = json.loads(raw.decode('utf-8'), object_pairs_hook=self._unique_pairs,
                                   parse_constant=lambda _: _fail())
                if value.get('task_id') != task_id:
                    _fail()
                return _validate(value) if validate else value
            except LLMToolError:
                raise
            except FileNotFoundError:
                raise _TaskNotFound('task not found') from None
            except OSError as exc:
                if _is_contention_error(exc):
                    time.sleep(TASK_UPDATE_LOCK_POLL_SECONDS)
                    continue
                _fail()
            except Exception:
                _fail()
            finally:
                if fd is not None:
                    os.close(fd)
        _fail('TASK_STORE_BUSY')

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
                self._replace_at(directory, temporary, name)
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

    @staticmethod
    def _replace_at(directory, temporary, name):
        """Rename the fsynced temporary over ``name``, waiting out transient contention.

        Only contention errnos are retried, within the TaskStore update-lock
        wait; the temporary is untouched by a failed rename, so each attempt
        installs the same bytes. Persistent contention fails closed BUSY; any
        other error propagates to the caller's UNAVAILABLE/CONFLICT mapping.
        """
        deadline = time.monotonic() + TASK_UPDATE_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                return
            except OSError as exc:
                if not _is_contention_error(exc):
                    raise
                if time.monotonic() >= deadline:
                    _fail('TASK_STORE_BUSY')
                time.sleep(TASK_UPDATE_LOCK_POLL_SECONDS)

    def create(self, record):
        if type(record) is dict and 'abandonment' in record:
            _fail('TASK_ABANDONMENT_MUTATION_FORBIDDEN')
        if (type(record) is not dict or 'dispatch_audit' in record or
                'completion_events' in record or 'completion_event_tombstones' in record or
                'pm_session_adoptions' in record):
            _fail()
        record = dict(record)
        # The owner gate runs first: a denied developer is cleared, so the
        # record gains no D6 and its audited denial never waits on, or is
        # refused by, the binding lock.
        denial = self._gate_created_developer(record)
        claims = self._d6_live_ids(record)
        if claims:
            saved = self._d6_commit(record['task_id'], claims, self._d6_bind_holds,
                                    lambda _authorized: self._write(record, create=True))
        else:
            saved = self._write(record, create=True)
        if denial is not None:
            raise denial
        return saved

    @staticmethod
    def _gate_created_developer(record):
        """Apply the dispatch gate to a created developer; return its denial."""
        developer = record.get('developer')
        if type(developer) is not dict:
            return None
        from . import worker_dispatch
        gate_record = {'task_id': record['task_id'], 'developer': None,
                       'dispatch_audit': []}
        if not worker_dispatch.task_store_attempts(gate_record, {'developer': developer}):
            return None
        denial = worker_dispatch.authorize_task_store_change(
            gate_record, {'developer': developer})
        record['dispatch_audit'] = gate_record['dispatch_audit']
        if denial is not None:
            record['developer'] = None
            if 'completion' in record:
                record['completion'] = None
        return denial

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

    def completion_d6_task_ids(self, executor_task_id):
        """Return sorted IDs of Tasks whose stored completion names this D6.

        Read-only. Each record gets the file checks and strict JSON parse of
        _read_at but not _validate, so a scan does not grow with full
        validation of unrelated records. A returned record is not validated:
        the caller must read it through get() before using it. Nothing is
        skipped: a record that cannot be opened, read or parsed, or whose
        task_id does not match its file name, raises. An invalid D6 raises.
        """
        if not _safe_identifier(executor_task_id, D6_TASK_ID):
            _fail()
        task_ids = self.task_ids()
        directory = self._dirfd()
        try:
            matching = []
            for task_id in task_ids:
                record = self._read_at(directory, task_id, validate=False)
                completion = record.get('completion')
                if (type(completion) is dict and
                        completion.get('executor_task_id') == executor_task_id):
                    matching.append(task_id)
        finally:
            os.close(directory)
        return sorted(matching)

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

    @staticmethod
    def _claude_d6_holds(record, executor_task_id):
        if record.get('abandonment') is not None:
            return False
        dev = record.get('developer')
        completion = record.get('completion')
        return ((type(dev) is dict and
                 dev.get('executor_task_id') == executor_task_id) or
                (type(completion) is dict and
                 completion.get('executor_task_id') == executor_task_id))

    @staticmethod
    def _d6_bind_holds(record, executor_task_id):
        if record.get('abandonment') is not None:
            return False
        dev = record.get('developer')
        if type(dev) is not dict:
            return False
        completion = record.get('completion')
        callback = dev.get('direct_result_callback')
        bound_ids = (
            dev.get('executor_task_id'),
            completion.get('executor_task_id') if type(completion) is dict else None,
            callback.get('executor_task_id') if type(callback) is dict else None,
        )
        state = dev.get('state')
        execution = dev.get('execution')
        live = (state in ('DEVELOPER_PENDING', 'DEVELOPER_DISPATCHING') or
                (state == 'DEVELOPER_OUTCOME_UNKNOWN' and
                 (type(execution) is not dict or
                  execution.get('cleanup_status') != 'CONFIRMED')))
        return executor_task_id in bound_ids and live

    @staticmethod
    def _claude_bind_holds(record, executor_task_id):
        return (TaskStore._claude_d6_holds(record, executor_task_id) or
                TaskStore._d6_bind_holds(record, executor_task_id))

    @staticmethod
    def _d6_live_ids(record):
        """Return every D6 this record holds as a live holder."""
        developer = record.get('developer')
        callback = (developer.get('direct_result_callback')
                    if type(developer) is dict else None)
        bound = (item.get('executor_task_id')
                 for item in (developer, record.get('completion'), callback)
                 if type(item) is dict)
        return frozenset(value for value in bound if type(value) is str and
                         TaskStore._d6_bind_holds(record, value))

    def _read_d6_journal_at(self, directory):
        """Read the claim journal; anything unreadable refuses the bind."""
        try:
            record = self._read_sidecar_at(
                directory, D6_BINDING_JOURNAL_NAME,
                max_bytes=D6_BINDING_JOURNAL_MAX_BYTES, code='TASK_D6_BINDING')
        except LLMToolError:
            _fail('TASK_D6_BINDING_SCAN_FAILED')
        if record is None:
            return {'epoch': 0, 'entries': []}
        valid = (type(record) is dict and set(record) == _D6_BINDING_JOURNAL_FIELDS and
                 record['schema_version'] == D6_BINDING_JOURNAL_SCHEMA_VERSION and
                 type(record['epoch']) is int and type(record['entries']) is list and
                 len(record['entries']) <= D6_BINDING_JOURNAL_MAX_ENTRIES and
                 record['epoch'] >= len(record['entries']) and
                 record['record_sha256'] == _digest(record))
        if valid:
            first = record['epoch'] - len(record['entries']) + 1
            valid = all(
                type(entry) is dict and set(entry) == _D6_BINDING_ENTRY_FIELDS and
                entry['epoch'] == first + index and
                type(entry['d6_sha256']) is str and
                re.fullmatch(r'[0-9a-f]{64}', entry['d6_sha256']) is not None and
                _safe_identifier(entry['task_id'], TASK_ID)
                for index, entry in enumerate(record['entries']))
        if not valid:
            _fail('TASK_D6_BINDING_SCAN_FAILED')
        return record

    def _append_d6_journal_at(self, directory, journal, task_id, claims):
        epoch = journal['epoch']
        entries = list(journal['entries'])
        for claim in sorted(claims):
            epoch += 1
            entries.append({'epoch': epoch, 'd6_sha256': _d6_digest(claim),
                            'task_id': task_id})
        value = {'schema_version': D6_BINDING_JOURNAL_SCHEMA_VERSION, 'epoch': epoch,
                 'entries': entries[-D6_BINDING_JOURNAL_MAX_ENTRIES:]}
        value['record_sha256'] = _digest(value)
        self._write_sidecar_at(directory, D6_BINDING_JOURNAL_NAME, _json_bytes(value),
                               code='TASK_D6_BINDING')

    def _d6_journal_epoch(self, directory):
        # Read under the lock so no claim at or below this epoch is still
        # waiting to commit: the scan that follows is certain to see it.
        lock_fd = self._lock_sidecar(directory, D6_BINDING_LOCK_NAME,
                                     exclusive=False, code='TASK_D6_BINDING')
        try:
            return self._read_d6_journal_at(directory)['epoch']
        finally:
            self._unlock_sidecar(lock_fd)

    def _d6_scan_holders(self, task_id, claims, holds):
        """Return the other Tasks holding a claimed D6. Runs without the lock."""
        holders = set()
        try:
            for other_id in self.task_ids():
                if other_id == task_id:
                    continue
                other = self.get(other_id)
                if any(holds(other, claim) for claim in claims):
                    holders.add(other_id)
        except Exception:
            _fail('TASK_D6_BINDING_SCAN_FAILED')
        return holders

    def _d6_refuse_locked_holders(self, task_id, claims, holds, holders, journal,
                                  seen_epoch):
        """Re-read only scan holders and same-D6 claims newer than the scan.

        A journal claim whose Task has no record holds nothing, so it is
        skipped (see ``_d6_commit``). A scan holder was read moments ago, so
        its record going missing still refuses, as does any other read failure.
        """
        digests = {_d6_digest(claim) for claim in claims}
        recheck = set(holders)
        recheck.update(entry['task_id'] for entry in journal['entries']
                       if entry['epoch'] > seen_epoch and entry['d6_sha256'] in digests)
        recheck.discard(task_id)
        for other_id in sorted(recheck):
            try:
                other = self.get(other_id)
            except _TaskNotFound:
                if other_id in holders:
                    _fail('TASK_D6_BINDING_SCAN_FAILED')
                continue
            except Exception:
                _fail('TASK_D6_BINDING_SCAN_FAILED')
            if any(holds(other, claim) for claim in claims):
                _fail('TASK_D6_ALREADY_BOUND', holder_task_id=other_id)

    def _d6_commit(self, task_id, claims, holds, commit):
        """Run ``commit`` as the one writer gaining ``claims``, or refuse.

        The full scan runs without the lock; the lock covers only a bounded
        journal read, the re-read of what the scan found or the journal added,
        one journal append and ``commit``. No Task is assumed to stay a
        non-holder. ``_update_record`` refuses, under the Task lock, any commit
        that gains a live D6 ``commit`` was not given here, so every gain has
        a journal entry written before it and commits inside the binding lock.

        So if P gained a D6 and is still live when Q tries to, one of two cases
        holds. Q's epoch came after P's entry: P had committed, and Q's scan
        finds it. Q's epoch came first: P's entry is newer than the scan, and Q
        re-reads P under the lock. Either way Q refuses. A journal that has
        dropped entries newer than the scan forces a new scan.

        The entry must stay first: a commit with no entry yet is invisible to a
        binder whose scan already passed that Task. So a writer that stops
        between the two leaves an entry that gained nothing. Only the lock
        holder can be between them, and the lock dies with its process, so
        under the lock every entry's writer has finished. Its Task then either
        has a record, re-read as usual, or never got one and holds nothing. A
        retry of that Task is a new commit through this same path.
        """
        self._name(task_id)
        directory = self._dirfd()
        try:
            for _ in range(D6_BINDING_ATTEMPTS):
                seen_epoch = self._d6_journal_epoch(directory)
                holders = self._d6_scan_holders(task_id, claims, holds)
                lock_fd = self._lock_sidecar(directory, D6_BINDING_LOCK_NAME,
                                             exclusive=True, code='TASK_D6_BINDING')
                try:
                    journal = self._read_d6_journal_at(directory)
                    if journal['epoch'] < seen_epoch:
                        _fail('TASK_D6_BINDING_SCAN_FAILED')
                    if seen_epoch < journal['epoch'] - len(journal['entries']):
                        continue
                    self._d6_refuse_locked_holders(task_id, claims, holds, holders,
                                                   journal, seen_epoch)
                    self._append_d6_journal_at(directory, journal, task_id, claims)
                    return commit(claims)
                except _D6ClaimRequired as required:
                    claims = claims | required.claims
                finally:
                    self._unlock_sidecar(lock_fd)
            _fail('TASK_D6_BINDING_BUSY')
        finally:
            os.close(directory)

    def _d6_bind(self, task_id, *, executor_task_id, expected, holds,
                 abandoned_target_code, **changes):
        if (type(executor_task_id) is not str or not executor_task_id or
                type(expected) is not dict or not expected):
            _fail('TASK_D6_BINDING_SCAN_FAILED')
        # A target that is abandoned or no longer matches ``expected`` can
        # never bind: refuse it before the scan, the binding lock and a
        # journal claim. ``commit`` re-checks both under the lock.
        try:
            target = self.get(task_id)
        except LLMToolError:
            raise
        except Exception:
            _fail('TASK_D6_BINDING_SCAN_FAILED')
        if target.get('abandonment') is not None:
            _fail(abandoned_target_code)
        if any(target.get(key) != value for key, value in expected.items()):
            raise TaskStoreConflict() from None

        def commit(claims):
            try:
                target = self.get(task_id)
                if target.get('abandonment') is not None:
                    _fail(abandoned_target_code)
                update_expected = dict(expected)
                update_expected['record_sha256'] = target['record_sha256']
                try:
                    return self._update(task_id, expected=update_expected,
                                        changes=dict(changes), d6_claims=claims)
                except TaskStoreConflict:
                    if self.get(task_id).get('abandonment') is not None:
                        _fail(abandoned_target_code)
                    raise
            except LLMToolError:
                raise
            except Exception:
                _fail('TASK_D6_BINDING_SCAN_FAILED')

        return self._d6_commit(task_id, frozenset((executor_task_id,)), holds, commit)

    def claude_bind_compare_and_update(self, task_id, *, executor_task_id,
                                       expected, **changes):
        """Atomically ensure a D6 is not already held by another live Task."""
        return self._d6_bind(
            task_id, executor_task_id=executor_task_id, expected=expected,
            holds=self._claude_bind_holds,
            abandoned_target_code='CLAUDE_TASK_ABANDONED', **changes)

    def d6_bind_compare_and_update(self, task_id, *, executor_task_id,
                                   expected, **changes):
        """Atomically bind a direct-route Task to an available D6 executor."""
        return self._d6_bind(
            task_id, executor_task_id=executor_task_id, expected=expected,
            holds=self._d6_bind_holds,
            abandoned_target_code='TASK_D6_ALREADY_BOUND', **changes)

    def record_published_pr(self, task_id, pr, head_sha):
        """Record or replace the PR linked to this Task's published HEAD."""
        if (type(pr) is not int or not 1 <= pr <= 9999999 or
                type(head_sha) is not str or
                re.fullmatch(r'[0-9a-f]{40}', head_sha) is None):
            _fail('TASK_PUBLISHED_PR_INVALID')
        for _ in range(5):
            current = self.get(task_id)
            existing = current.get('published_pr')
            if (type(existing) is dict and existing.get('pr') == pr and
                    existing.get('head_sha') == head_sha):
                return existing
            published = {
                'pr': pr, 'head_sha': head_sha,
                'recorded_at': datetime.now(timezone.utc).replace(
                    microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ'),
            }
            try:
                self._update(task_id, expected={'record_sha256': current['record_sha256']},
                             changes={'published_pr': published})
                return published
            except TaskStoreConflict:
                continue
        _fail('TASK_STORE_BUSY')

    def record_publish_operation(self, task_id, operation):
        """Append one publish operation record; the oldest drops beyond the cap."""
        if not _valid_publish_operation(operation):
            _fail('TASK_PUBLISH_OPERATION_INVALID')
        for _ in range(5):
            current = self.get(task_id)
            operations = list(current.get('publish_operations', []))
            operations = (operations + [dict(operation)])[-MAX_PUBLISH_OPERATIONS:]
            try:
                self._update(task_id, expected={'record_sha256': current['record_sha256']},
                             changes={'publish_operations': operations})
                return operation
            except TaskStoreConflict:
                continue
        _fail('TASK_STORE_BUSY')

    def record_repair_cap_override(self, task_id, *, ref, rounds, recorded_at=None):
        """Persist the trusted PM reference that raises this Task's repair cap."""
        item = {'ref': ref, 'rounds': rounds,
                'recorded_at': (datetime.now(timezone.utc).replace(
                    microsecond=0).strftime(_UTC_SECONDS)
                    if recorded_at is None else recorded_at)}
        for _ in range(4):
            current = self.get(task_id)
            try:
                return self._update(
                    task_id, expected={'record_sha256': current['record_sha256']},
                    changes={'repair_cap_override': item})
            except TaskStoreConflict:
                continue
        _fail('TASK_STORE_BUSY')

    def record_codex_launch(self, task_id, *, assignment_id, pid, start_time, pgid):
        """Persist the direct Codex child identity through the trusted launch path."""
        return self._record_direct_launch(
            task_id, assignment_id=assignment_id, pid=pid, start_time=start_time,
            pgid=pgid, provider_profile='codex', trusted_flag='trusted_codex_launch')

    def record_opencode_launch(self, task_id, *, assignment_id, pid, start_time, pgid):
        """Persist the direct OpenCode child identity through the trusted launch path."""
        return self._record_direct_launch(
            task_id, assignment_id=assignment_id, pid=pid, start_time=start_time,
            pgid=pgid, provider_profile='opencode',
            trusted_flag='trusted_opencode_launch')

    def _record_direct_launch(self, task_id, *, assignment_id, pid, start_time, pgid,
                              provider_profile, trusted_flag):
        if (not _safe_identifier(assignment_id, TASK_ID) or type(pid) is not int or
                pid <= 0 or type(pgid) is not int or pgid <= 0 or
                type(start_time) is not str or not 1 <= len(start_time) <= 64 or
                re.fullmatch(r'[A-Za-z0-9 :_-]+', start_time) is None):
            _fail('CODEX_LAUNCH_IDENTITY_INVALID')
        current = self.get(task_id)
        developer = current.get('developer')
        if (type(developer) is not dict or developer.get('assignment_id') != assignment_id or
                developer.get('provider_profile') != provider_profile or
                developer.get('state') != 'DEVELOPER_DISPATCHING' or
                developer.get('execution') is not None):
            _fail('CODEX_LAUNCH_IDENTITY_INVALID')
        execution = {
            'status': 'RUNNING', 'native_session_id': None,
            'process_exit_code': None, 'stdout_sha256': None, 'elapsed_ms': None,
            'cleanup_status': 'UNKNOWN', 'usage_source': 'UNKNOWN',
            'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None, 'child_pid': pid,
            'child_start_time': start_time, 'child_pgid': pgid,
        }
        developer = dict(developer, execution=execution)
        return self._update(task_id, expected={'record_sha256': current['record_sha256']},
                            changes={'developer': developer}, **{trusted_flag: True})

    def adopt_pm_session(self, task_id, *, old_session_key, new_session_key, at,
                         expected_record_sha256, old_runtime, new_runtime, new_account,
                         liveness):
        """Library-internal: atomically move one PM binding, inbox and audit entry."""
        current = self.get(task_id)
        if current.get('pm_session_id') != old_session_key:
            raise TaskStoreConflict() from None
        if len(current.get('pm_session_adoptions', [])) >= 32:
            _fail('PM_SESSION_ADOPTION_LIMIT')
        audit = list(current.get('pm_session_adoptions', []))
        audit.append({'old_session_key': old_session_key,
                      'new_session_key': new_session_key, 'at': at,
                      'old_runtime': old_runtime, 'new_runtime': new_runtime,
                      'new_account': new_account, 'liveness': list(liveness)})
        events = current.get('completion_events')
        changes = {'pm_session_id': new_session_key, 'pm_session_adoptions': audit}
        provenance = current.get('pm_provenance')
        if type(provenance) is dict and 'pm_native_session_id' in provenance:
            changes['pm_provenance'] = dict(
                provenance, pm_runtime=new_runtime,
                pm_native_session_id=new_session_key[3:])
        if events is not None:
            changes['completion_events'] = [
                dict(event, pm_session_id=new_session_key) for event in events]
        return self._update(task_id,
            expected={'record_sha256': expected_record_sha256,
                      'pm_session_id': old_session_key}, changes=changes,
            trusted_completion_events=events is not None,
            trusted_pm_session_adoption=True)

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
            # Only the current PM session records new receipts; earlier
            # sessions of an adoption chain are accepted on read only.
            if receipt.get('pm_session') != current.get('pm_session_id'):
                _fail('REMOTE_TEST_RECEIPT_INVALID')
            if len(receipts) >= 32:
                _fail('REMOTE_TEST_RECEIPT_LIMIT')
            try:
                self._update(task_id, expected={'record_sha256': current['record_sha256']},
                             changes={'remote_test_receipts': receipts + [receipt]})
                return receipt
            except TaskStoreConflict:
                continue
        _fail('TASK_STORE_BUSY')

    def record_claude_result(self, task_id, *, pm_session_id, executor_task_id, head_sha):
        """Record the one PM-bound result transition for a Claude Developer Task."""
        if (not _safe_identifier(pm_session_id, SESSION_ID) or
                not _safe_identifier(executor_task_id, D6_TASK_ID) or
                type(head_sha) is not str or re.fullmatch(r'[0-9a-f]{40}', head_sha) is None):
            _fail('CLAUDE_RESULT_INVALID')
        for _ in range(5):
            current = self.get(task_id)
            if current.get('abandonment') is not None:
                _fail('CLAUDE_TASK_ABANDONED')
            developer = current.get('developer')
            if current.get('pm_session_id') != pm_session_id:
                _fail('CLAUDE_RESULT_BINDING_MISMATCH')
            if (type(developer) is not dict or
                    developer.get('provider_profile') != 'claude' or
                    developer.get('backend_session_semantics') != 'NO_NATIVE_SESSION_ID_EXPOSED' or
                    developer.get('state') != 'DEVELOPER_PENDING' or
                    developer.get('executor_task_id') != executor_task_id):
                _fail('CLAUDE_RESULT_STATE_INVALID')
            responded = dict(developer)
            responded.update(state='DEVELOPER_RESPONDED', claude_result={
                'executor_task_id': executor_task_id, 'head_sha': head_sha,
                'recorded_at': self._completion_now(),
            })
            try:
                return self._update(task_id,
                    expected={'record_sha256': current['record_sha256']},
                    changes={'developer': responded})
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

    def _read_sidecar_at(self, directory, name, *, max_bytes, code):
        """Read one owner-only JSON sidecar in the store root; None when absent.

        ``code`` prefixes the fail-closed error codes (``<code>_INVALID`` etc.).
        """
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
        except FileNotFoundError:
            return None
        except OSError:
            _fail(code + '_UNAVAILABLE')
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                    stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
                    before.st_size > max_bytes):
                os.close(fd)
                fd = None
                _fail(code + '_INVALID')
            with os.fdopen(fd, 'rb') as stream:
                fd = None
                raw = stream.read(max_bytes + 1)
                after = os.fstat(stream.fileno())
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (len(raw) > max_bytes or
                    _identity(before) != _identity(after) or
                    _identity(before) != _identity(current)):
                _fail(code + '_CHANGED')
            return json.loads(raw.decode('utf-8'),
                              object_pairs_hook=self._unique_pairs,
                              parse_constant=lambda _: _fail(code + '_INVALID'))
        except LLMToolError:
            raise
        except Exception:
            _fail(code + '_INVALID')
        finally:
            if fd is not None:
                os.close(fd)

    def _read_worker_availability_at(self, directory):
        record = self._read_sidecar_at(directory, WORKER_AVAILABILITY_NAME,
                                       max_bytes=WORKER_AVAILABILITY_MAX_BYTES,
                                       code='WORKER_AVAILABILITY')
        if record is None:
            return {'schema_version': WORKER_AVAILABILITY_SCHEMA_VERSION,
                    'observations': [], 'record_sha256': None}
        try:
            return self._validate_worker_availability(record)
        except LLMToolError:
            raise
        except Exception:
            _fail('WORKER_AVAILABILITY_INVALID')

    @staticmethod
    def _lock_sidecar(directory, lock_name, *, exclusive, code):
        """Take a shared or exclusive flock on a sidecar lock file in the store root.

        Both the open and the flock are bounded by the TaskStore update-lock
        deadline; a holder past it fails closed with ``<code>_BUSY``.
        """
        deadline = time.monotonic() + TASK_UPDATE_LOCK_TIMEOUT_SECONDS
        lock_fd = None
        try:
            while True:
                try:
                    lock_fd = os.open(
                        lock_name,
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
                lock_fd = None
                _fail(code + '_INVALID')
            mode = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
            while True:
                try:
                    fcntl.flock(lock_fd, mode)
                    return lock_fd
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        os.close(lock_fd)
                        lock_fd = None
                        _fail(code + '_BUSY')
                    time.sleep(TASK_UPDATE_LOCK_POLL_SECONDS)
        except LLMToolError:
            raise
        except OSError:
            if lock_fd is not None:
                os.close(lock_fd)
            _fail(code + '_UNAVAILABLE')

    @staticmethod
    def _unlock_sidecar(lock_fd):
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

    @staticmethod
    def _lock_worker_availability(directory, *, exclusive):
        return TaskStore._lock_sidecar(directory, WORKER_AVAILABILITY_LOCK_NAME,
                                       exclusive=exclusive, code='WORKER_AVAILABILITY')

    @staticmethod
    def _write_sidecar_at(directory, name, raw, *, code):
        """Atomically replace one sidecar (temp file, fsync, rename, dir fsync)."""
        temporary = name + '.' + uuid.uuid4().hex + '.tmp'
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        except LLMToolError:
            raise
        except OSError:
            _fail(code + '_UNAVAILABLE')
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass

    def _write_worker_availability_at(self, directory, record):
        value = dict(record)
        value.pop('record_sha256', None)
        value['record_sha256'] = _digest(value)
        self._validate_worker_availability(value)
        raw = _json_bytes(value)
        if len(raw) > WORKER_AVAILABILITY_MAX_BYTES:
            _fail('WORKER_AVAILABILITY_LIMIT')
        self._write_sidecar_at(directory, WORKER_AVAILABILITY_NAME, raw,
                               code='WORKER_AVAILABILITY')
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

    def _read_assignments_at(self, directory):
        record = self._read_sidecar_at(directory, ASSIGNMENTS_NAME,
                                       max_bytes=ASSIGNMENTS_MAX_BYTES, code='ASSIGNMENTS')
        if record is None:
            return []
        return _validate_assignments(record)['assignments']

    def _write_assignments_at(self, directory, assignments):
        value = {'schema_version': ASSIGNMENTS_SCHEMA_VERSION, 'assignments': assignments}
        value['record_sha256'] = _digest(value)
        _validate_assignments(value)
        raw = _json_bytes(value)
        if len(raw) > ASSIGNMENTS_MAX_BYTES:
            _fail('ASSIGNMENT_LIMIT')
        self._write_sidecar_at(directory, ASSIGNMENTS_NAME, raw, code='ASSIGNMENTS')

    def _with_assignments(self, *, exclusive, change=None):
        directory = self._dirfd()
        lock_fd = None
        try:
            lock_fd = self._lock_sidecar(directory, ASSIGNMENTS_LOCK_NAME,
                                         exclusive=exclusive, code='ASSIGNMENTS')
            assignments = self._read_assignments_at(directory)
            if change is None:
                return json.loads(json.dumps(assignments))
            # ``change`` returns (updated or None, result, error or None): a
            # None update writes nothing; an error is raised after the write so
            # a refusal can still persist its audit or an EXPIRED transition.
            updated, result, error = change(json.loads(json.dumps(assignments)))
            if updated is not None:
                self._write_assignments_at(directory, updated)
            if error is not None:
                raise error
            return result
        finally:
            self._unlock_sidecar(lock_fd)
            os.close(directory)

    def get_developer_assignments(self):
        """Read every stored Developer assignment (ASSIGNED, CLAIMED and EXPIRED)."""
        return self._with_assignments(exclusive=False)

    @staticmethod
    def _assignment_live(item, now, source_sha=None):
        """One writer per D6: an unexpired ASSIGNED, or a CLAIMED at the same HEAD."""
        return ((item['state'] == 'ASSIGNED' and now < _utc_seconds(item['expires_at'])) or
                (item['state'] == 'CLAIMED' and item['source_sha'] == source_sha))

    def record_developer_assignment(self, *, worker, d6_task_id, common_dir, pm_session_id,
                                    assigned_at, expires_at, gate, task_id=None,
                                    owner_assignment_ref=None):
        """Write one ASSIGNED event bound to a D6 task, only after ``gate`` allows it.

        ``gate()`` runs under the exclusive assignment lock. It raises on a
        dispatch-gate or D6 refusal (nothing is written) and otherwise returns
        ``{'dispatch': {...}, 'd6_root': str, 'source_sha': str}``. A live
        assignment for the same D6 task (any worker) is refused, so one D6
        has one writer.
        """
        placeholder = {'decision': 'ALLOWED', 'reason_code': 'OWNER_POLICY_ALLOWED',
                       'policy_sha256': '0' * 64}
        candidate = {
            'assignment_id': new_id(), 'event': 'ASSIGNED', 'worker': worker,
            'd6_task_id': d6_task_id, 'common_dir': common_dir, 'd6_root': '/placeholder',
            'source_sha': '0' * 40, 'pm_session_id': pm_session_id, 'task_id': task_id,
            'owner_assignment_ref': owner_assignment_ref, 'assigned_at': assigned_at,
            'expires_at': expires_at, 'dispatch': placeholder, 'state': 'ASSIGNED',
            'claim': None,
            'audit': [dict(placeholder, action='ASSIGN', at=assigned_at)],
        }
        # Validate the caller's input shape before taking the lock or the gate.
        _validate_assignment(candidate)
        now = _utc_seconds(assigned_at)

        def change(assignments):
            bound = gate()
            if type(bound) is not dict or set(bound) != {'dispatch', 'd6_root', 'source_sha'}:
                _fail('ASSIGNMENTS_INVALID')
            if any(item['d6_task_id'] == d6_task_id and item['common_dir'] == common_dir and
                   self._assignment_live(item, now, bound['source_sha'])
                   for item in assignments):
                _fail('ASSIGNMENT_DUPLICATE')
            candidate.update(bound)
            candidate['audit'] = [dict(bound['dispatch'], action='ASSIGN', at=assigned_at)]
            _validate_assignment(candidate)
            if len(assignments) >= MAX_ASSIGNMENTS:
                # Compact the oldest finished record; never drop a live assignment.
                oldest = next((item for item in assignments
                               if not self._assignment_live(item, now)), None)
                if oldest is None:
                    _fail('ASSIGNMENT_LIMIT')
                assignments.remove(oldest)
            return assignments + [candidate], json.loads(json.dumps(candidate)), None

        return self._with_assignments(exclusive=True, change=change)

    def claim_developer_assignment(self, assignment_id, *, worker, d6_task_id, session_id,
                                   account_dir_label, clock, gate):
        """One-shot ASSIGNED -> CLAIMED with a receipt; ASSIGNED -> EXPIRED when late.

        ``clock()`` returns the current UTC-seconds stamp; it is read once
        under the exclusive lock, so time spent waiting for the lock counts
        toward expiry, and the same stamp is the receipt's ``claimed_at``.
        ``gate(item)`` runs under the exclusive lock and returns
        ``(audit_entry, error)``: the dispatch-gate result is appended to the
        assignment's bounded audit either way, and ``error`` (dispatch, D6 or
        transcript refusal) is raised after that write. A repeated claim by the
        same session and account returns the stored receipt unchanged.
        """
        if not _safe_identifier(assignment_id, TASK_ID):
            _fail('ASSIGNMENT_ID_INVALID')
        if (not _safe_identifier(session_id, PM_NATIVE_SESSION_ID) or
                type(account_dir_label) is not str or
                re.fullmatch(r'[0-9a-f]{16}', account_dir_label) is None or
                not callable(clock)):
            _fail('ASSIGNMENT_CLAIM_INVALID')

        def refused(code):
            try:
                _fail(code)
            except LLMToolError as error:
                return error

        def change(assignments):
            claimed_at = clock()
            if not _valid_utc_seconds(claimed_at):
                return None, None, refused('ASSIGNMENT_CLAIM_INVALID')
            now = _utc_seconds(claimed_at)
            item = next((item for item in assignments
                         if item['assignment_id'] == assignment_id), None)
            if item is None:
                return None, None, refused('ASSIGNMENT_NOT_FOUND')
            if item['worker'] != worker:
                return None, None, refused('ASSIGNMENT_WORKER_MISMATCH')
            if item['d6_task_id'] != d6_task_id:
                return None, None, refused('ASSIGNMENT_D6_MISMATCH')
            if item['state'] == 'CLAIMED':
                claim = item['claim']
                if (claim['session_id'] == session_id and
                        claim['account_dir_label'] == account_dir_label):
                    return None, item, None
                return None, None, refused('ASSIGNMENT_ALREADY_CLAIMED')
            if item['state'] == 'EXPIRED':
                return None, None, refused('ASSIGNMENT_EXPIRED')
            if now >= _utc_seconds(item['expires_at']):
                item['state'] = 'EXPIRED'
                _validate_assignment(item)
                return assignments, None, refused('ASSIGNMENT_EXPIRED')
            entry, error = gate(json.loads(json.dumps(item)))
            item['audit'] = (item['audit'] + [_validate_assignment_audit(dict(
                entry, action='CLAIM', at=claimed_at))])[-MAX_ASSIGNMENT_AUDIT:]
            if error is None and entry['decision'] != 'ALLOWED':
                error = refused('ASSIGNMENT_DISPATCH_BLOCKED')
            if error is None:
                item['state'] = 'CLAIMED'
                item['claim'] = {'session_id': session_id, 'claimed_at': claimed_at,
                                 'account_dir_label': account_dir_label}
            _validate_assignment(item)
            return assignments, (None if error else json.loads(json.dumps(item))), error

        return self._with_assignments(exclusive=True, change=change)

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

    def _update(self, task_id, *, expected, changes, d6_claims=None, **trusted):
        """Commit one change; a commit gaining a live D6 goes through ``_d6_commit``."""
        if d6_claims is not None:
            return self._update_record(task_id, expected=expected, changes=changes,
                                       d6_claims=d6_claims, **trusted)
        try:
            return self._update_record(task_id, expected=expected, changes=changes,
                                       d6_claims=frozenset(), **trusted)
        except _D6ClaimRequired as required:
            claims = required.claims
        return self._d6_commit(
            task_id, claims, self._d6_bind_holds,
            lambda authorized: self._update_record(
                task_id, expected=expected, changes=changes,
                d6_claims=authorized, **trusted))

    def _update_record(self, task_id, *, expected, changes, d6_claims,
                       trusted_dispatch_audit=False,
                       trusted_completion_events=False, trusted_abandonment=False,
                       abandonment_pm_session_id=None, trusted_pm_session_adoption=False,
                       trusted_codex_launch=False, trusted_opencode_launch=False):
        # Refused here, not by the writable-field check: the dispatch gate
        # before that check can write its audit to the record.
        if _D6_CLAIM_ARGUMENTS & set(changes):
            _fail()
        if ('developer' in changes and type(changes['developer']) is dict and
                'execution' in changes['developer'] and
                type(changes['developer'].get('execution')) is dict and
                'child_pid' in changes['developer']['execution'] and
                not (trusted_codex_launch or trusted_opencode_launch)):
            _fail('CODEX_LAUNCH_IDENTITY_FORBIDDEN')
        if (trusted_codex_launch or trusted_opencode_launch) and set(changes) != {'developer'}:
            _fail('CODEX_LAUNCH_IDENTITY_INVALID')
        if 'dispatch_audit' in changes and not trusted_dispatch_audit:
            _fail()
        if 'merge_attempt' in changes:
            proposed = changes['merge_attempt']
            if type(proposed) is not dict:
                _fail('TASK_MERGE_ATTEMPT_INVALID')
        if (('completion_events' in changes or 'completion_event_tombstones' in changes)
                and not trusted_completion_events):
            _fail('COMPLETION_EVENT_MUTATION_FORBIDDEN')
        if 'abandonment' in changes and not trusted_abandonment:
            _fail('TASK_ABANDONMENT_MUTATION_FORBIDDEN')
        if ({'pm_provenance', 'pm_session_adoptions'} & set(changes) and
                not trusted_pm_session_adoption):
            _fail('PM_SESSION_ADOPTION_INVALID')
        if trusted_abandonment and (set(changes) != {'abandonment'} or
                not _safe_identifier(abandonment_pm_session_id, SESSION_ID)):
            _fail('TASK_ABANDONMENT_MUTATION_FORBIDDEN')
        if trusted_pm_session_adoption and (
                not {'pm_session_id', 'pm_session_adoptions'}.issubset(changes) or
                set(changes) - {'pm_session_id', 'pm_session_adoptions', 'completion_events',
                                'pm_provenance'}):
            _fail('PM_SESSION_ADOPTION_INVALID')
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
            if trusted_codex_launch or trusted_opencode_launch:
                proposed = changes.get('developer')
                old = current.get('developer')
                execution = (proposed.get('execution')
                             if type(proposed) is dict else None)
                expected_profile = 'codex' if trusted_codex_launch else 'opencode'
                if (type(old) is not dict or old.get('provider_profile') != expected_profile or
                        old.get('state') != 'DEVELOPER_DISPATCHING' or
                        type(proposed) is not dict or
                        proposed.get('assignment_id') != old.get('assignment_id') or
                        proposed.get('state') != 'DEVELOPER_DISPATCHING' or
                        type(execution) is not dict or
                        set(execution) != _DIRECT_EXECUTION_LAUNCH_FIELDS or
                        execution.get('status') != 'RUNNING' or
                        old.get('execution') is not None):
                    _fail('CODEX_LAUNCH_IDENTITY_INVALID')
            if 'merge_attempt' in changes:
                old_attempt = current.get('merge_attempt')
                new_attempt = changes['merge_attempt']
                valid = (old_attempt is None and new_attempt.get('state') == 'CLAIMED')
                if type(old_attempt) is dict:
                    same_identity = all(old_attempt.get(key) == new_attempt.get(key)
                                        for key in ('pr', 'head_sha'))
                    valid = (
                        old_attempt.get('state') == 'CLAIMED' and
                        new_attempt.get('state') in ('UNKNOWN', 'MERGED') and
                        same_identity and
                        old_attempt.get('claimed_at') == new_attempt.get('claimed_at')) or (
                        old_attempt.get('state') == 'UNKNOWN' and
                        new_attempt.get('state') == 'CLAIMED' and
                        same_identity and
                        old_attempt.get('claimed_at') != new_attempt.get('claimed_at'))
                if not valid:
                    _fail('TASK_MERGE_ATTEMPT_TRANSITION_INVALID')
            if trusted_abandonment:
                item = changes['abandonment']
                if (current.get('pm_session_id') != abandonment_pm_session_id or
                        current.get('abandonment') is not None or
                        type(item) is not dict or
                        item.get('pm_session_id') != abandonment_pm_session_id):
                    _fail('TASK_PM_SESSION_DENIED')
            if trusted_pm_session_adoption and current.get('abandonment') is not None:
                _fail('PM_SESSION_ADOPTION_ABANDONED')
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
                                    'account_mode', 'account', 'claimed_at',
                                    'reviewer_task')) and
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
            immutable = {'schema_version', 'task_id', 'run_id', 'pm_session_id',
                         'created_at', 'task_contract', 'task_binding', 'record_sha256'}
            writable = FIELDS - immutable
            if trusted_pm_session_adoption:
                writable = writable | {'pm_session_id'}
            if set(changes) - writable:
                _fail()
            # Checked after every refusal above, so a change that can never
            # commit (an invalid transition, a dispatch denial) is refused or
            # audited here and never takes the binding lock. The gate re-runs
            # on the locked retry against the record as it is then.
            if 'developer' in changes or 'completion' in changes:
                gained = (self._d6_live_ids({**current, **changes}) -
                          self._d6_live_ids(current))
                if not gained <= d6_claims:
                    raise _D6ClaimRequired(gained)
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
