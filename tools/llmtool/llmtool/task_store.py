"""Owner-private durable storage for llmtool PM-intake tasks.

This is a local task record store, not an execution queue or authorization
ledger. A task's logical PM session is an llmtool identifier; it is not a claim
that the selected provider exposes a resumable native session.
"""

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .action_contract import TaskBinding
from .diagnostics import validate_provider_usage
from .errors import LLMToolError
from .task_verification import parse_task_contract
from .test_output_excerpts import _emitted_sensitive
from .task_completion import validate_host_worker_receipt

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
FIELDS = _FIELDS_V2_ROUTE_COMPLETION | frozenset(('dispatch_audit',))
_USAGE_FIELDS = frozenset(('prompt_tokens', 'completion_tokens', 'total_tokens'))
_LEGACY_DEVELOPER_FIELDS = frozenset((
    'assignment_id', 'selection_source', 'state', 'provider_profile',
    'provider_type', 'model', 'backend_session_semantics', 'session_id',
    'source_sha', 'source_sha_binding', 'read_paths', 'write_paths', 'acceptance', 'context_sha256',
    'egress_basis', 'input_bytes', 'max_tokens',
    'artifact_format', 'artifact', 'artifact_sha256', 'usage', 'error_code',
    'created_at',
))
_DEVELOPER_FIELDS = _LEGACY_DEVELOPER_FIELDS | frozenset(('execution',))
_DEVELOPER_CALLBACK_FIELDS = _DEVELOPER_FIELDS | frozenset(('direct_result_callback',))
_DIRECT_EXECUTION_FIELDS = frozenset((
    'status', 'native_session_id', 'process_exit_code', 'stdout_sha256',
    'elapsed_ms', 'cleanup_status', 'usage_source', 'source_bytes',
    'source_file_count', 'source_manifest_sha256',
))
_DEVELOPER_REJECTION_CODES = frozenset((
    'DEVELOPER_OUTPUT_REJECTED', 'DEVELOPER_OUTPUT_INVALID_ENVELOPE',
    'DEVELOPER_OUTPUT_INVALID_DIFF', 'DEVELOPER_OUTPUT_OUT_OF_SCOPE',
    'DEVELOPER_OUTPUT_SENSITIVE', 'DEVELOPER_OUTPUT_INVALID_TEXT',
    'DEVELOPER_OUTPUT_TOO_LARGE',
))
_PM_ASSIGNMENT_FIELDS = frozenset(('schema_version', 'route', 'risk', 'goal', 'acceptance'))
_PM_PROVENANCE_FIELDS = frozenset((
    'source', 'provider_invoked', 'native_session_id_status', 'usage_status',
    'session_restored', 'automatic_callback',
))


def _fail(code='TASK_STORE_INVALID'):
    raise LLMToolError('task store unavailable; details omitted') from None


class TaskStoreConflict(LLMToolError):
    """A compare-and-update precondition lost to another task writer."""


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


def _validate(record):
    if (type(record) is not dict or
            (record.get('schema_version') == 1 and set(record) != _FIELDS_V1) or
            (record.get('schema_version') == 2 and set(record) not in
             (_FIELDS_V2, _FIELDS_V2_COMPLETION, _FIELDS_V2_ROUTE,
              _FIELDS_V2_ROUTE_COMPLETION, *_FIELDS_V2_WITH_PROVENANCE,
              *_FIELDS_V2_WITH_DISPATCH_AUDIT)) or
            record.get('schema_version') not in (1, 2)):
        _fail()
    if (type(record['schema_version']) is not int or record['schema_version'] not in (1, 2) or
            not _safe_identifier(record['task_id'], TASK_ID) or
            not _safe_identifier(record['run_id'], TASK_ID) or
            not _safe_identifier(record['pm_session_id'], SESSION_ID) or
            type(record['state']) is not str or record['state'] not in STATES or
            type(record['created_at']) is not str or type(record['updated_at']) is not str):
        _fail()
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
            if (type(entry) is not dict or set(entry) != {
                    'attempt_id', 'worker_key', 'action', 'decision',
                    'reason_code', 'policy_sha256', 'at'} or
                    type(entry['attempt_id']) is not str or
                    re.fullmatch(r'[0-9a-f]{32}', entry['attempt_id']) is None or
                    entry['attempt_id'] in seen_attempts or
                    type(entry['worker_key']) is not str or
                    re.fullmatch(r'[a-z][a-z0-9-]{0,63}', entry['worker_key']) is None or
                    entry['action'] not in (
                        'DISPATCH', 'RETRY', 'REASSIGN', 'RECOVERY', 'REPAIR',
                        'REVIEWER_HANDOFF', 'REVIEWER_RETRY') or
                    entry['decision'] not in ('ALLOWED', 'BLOCKED') or
                    type(entry['reason_code']) is not str or
                    re.fullmatch(r'[A-Z0-9_]{1,64}', entry['reason_code']) is None or
                    (entry['policy_sha256'] is not None and
                     (type(entry['policy_sha256']) is not str or
                      re.fullmatch(r'[0-9a-f]{64}', entry['policy_sha256']) is None))):
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
            if set(record['usage']) != _USAGE_FIELDS:
                _fail()
            validate_provider_usage(record['usage'])
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
                    assignment['route'] not in ('company-gw', 'codex', 'opencode', 'none') or
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
            if (type(provenance) is not dict or set(provenance) != _PM_PROVENANCE_FIELDS or
                    provenance['source'] != 'ACTIVE_PM_RUNTIME' or
                    provenance['provider_invoked'] is not False or
                    provenance['native_session_id_status'] != 'UNAVAILABLE' or
                    provenance['usage_status'] != 'UNKNOWN' or
                    provenance['session_restored'] is not False or
                    provenance['automatic_callback'] is not False or
                    record['pm_provider_profile'] != 'active-pm-runtime' or
                    record['pm_provider_type'] != 'codex-cli' or
                    record['pm_model'] is not None or
                    record['pm_route_binding'] != {
                        'type': 'codex-cli', 'model': None, 'base_url': None} or
                    record['backend_session_semantics'] != 'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL' or
                    record['native_session_id'] is not None or
                    record['state'] != 'PM_RESPONDED' or
                    record['request'] is None or not record['response'] or
                    record['request_sha256'] is None or record['usage'] is not None or
                    record['error_code'] is not None or
                    type(assignment) is not dict or assignment.get('route') != 'opencode' or
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
            assignment_shapes = (
                _LEGACY_DEVELOPER_FIELDS, _DEVELOPER_FIELDS,
                _DEVELOPER_CALLBACK_FIELDS,
                _LEGACY_DEVELOPER_FIELDS | frozenset(('owner_assignment_ref',)),
                _DEVELOPER_FIELDS | frozenset(('owner_assignment_ref',)),
                _DEVELOPER_CALLBACK_FIELDS | frozenset(('owner_assignment_ref',)),
            )
            if type(developer) is not dict or set(developer) not in assignment_shapes:
                _fail()
            if (not _safe_identifier(developer['assignment_id'], TASK_ID) or
                    developer['selection_source'] not in ('PM', 'USER') or
                    developer['state'] not in ('DEVELOPER_PENDING', 'DEVELOPER_DISPATCHING',
                                               'DEVELOPER_RESPONDED',
                                               'DEVELOPER_UNAVAILABLE', 'DEVELOPER_OUTCOME_UNKNOWN',
                                               'DEVELOPER_OUTPUT_REJECTED') or
                    developer['provider_profile'] not in ('company-gw', 'opencode', 'claude') or
                    developer['provider_type'] not in (None, 'openai-compatible', 'opencode-cli') or
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
                if (developer['provider_profile'] != 'claude' or
                        type(ref) is not str or _OWNER_COMMENT_REF.fullmatch(ref) is None):
                    _fail()
            if (developer['provider_profile'] == 'company-gw' and
                    (developer['provider_type'] not in (None, 'openai-compatible') or
                     developer['backend_session_semantics'] != 'NO_NATIVE_SESSION_ID_EXPOSED')):
                _fail()
            if (developer['provider_profile'] == 'opencode' and
                    (developer['provider_type'] not in (None, 'opencode-cli') or
                     developer['backend_session_semantics'] != 'CLI_REPORTED_SESSION_ID' or
                     developer['model'] not in (None, 'opencode/deepseek-v4.1-flash-free'))):
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
                    if (developer['provider_profile'] != 'opencode' or
                            type(execution) is not dict or
                            set(execution) != _DIRECT_EXECUTION_FIELDS or
                            execution['status'] not in ('SUCCEEDED', 'FAILED', 'UNKNOWN') or
                            (execution['native_session_id'] is not None and
                             (type(execution['native_session_id']) is not str or
                              re.fullmatch(r'ses_[A-Za-z0-9]{1,100}',
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
                    if execution['status'] == 'SUCCEEDED' and (
                            execution['native_session_id'] is None or
                            execution['process_exit_code'] != 0 or
                            execution['stdout_sha256'] is None or
                            execution['source_bytes'] is None or
                            execution['source_file_count'] is None or
                            execution['source_manifest_sha256'] is None or
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
                        _emitted_sensitive(artifact)):
                    _fail()
            elif artifact_sha is not None:
                _fail()
            usage = developer['usage']
            if usage is not None:
                try:
                    if type(usage) is not dict or set(usage) != _USAGE_FIELDS:
                        _fail()
                    validate_provider_usage(usage)
                except Exception:
                    _fail()
            if developer['error_code'] is not None and (
                    type(developer['error_code']) is not str or
                    re.fullmatch(r'[A-Z0-9_]{1,64}', developer['error_code']) is None):
                _fail()
            dev_state = developer['state']
            expected_provider = ('openai-compatible' if
                                 developer['provider_profile'] == 'company-gw' else
                                 'opencode-cli')
            if dev_state == 'DEVELOPER_PENDING' and (
                    developer['provider_type'] is not None or developer['model'] is not None or
                    artifact is not None or usage is not None or developer['error_code'] is not None):
                _fail()
            if dev_state == 'DEVELOPER_DISPATCHING' and (
                    developer['provider_type'] != expected_provider or artifact is not None or
                    usage is not None or developer['error_code'] is not None or
                    developer['max_tokens'] is None):
                _fail()
            if dev_state == 'DEVELOPER_UNAVAILABLE' and (
                    artifact is not None or usage is not None or
                    developer['error_code'] is None):
                _fail()
            if dev_state == 'DEVELOPER_OUTCOME_UNKNOWN' and (
                    developer['provider_type'] != expected_provider or artifact is not None or
                    usage is not None or developer['error_code'] is None):
                _fail()
            if dev_state == 'DEVELOPER_RESPONDED' and (
                    developer['provider_type'] is None or artifact is None or
                    developer['error_code'] is not None or developer['max_tokens'] is None):
                _fail()
            if developer['provider_profile'] == 'opencode':
                execution = developer.get('execution')
                if dev_state == 'DEVELOPER_RESPONDED' and (
                        type(execution) is not dict or execution['status'] != 'SUCCEEDED' or
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
                          'PM_DISPATCHING', 'PM_RETURNED', 'PM_RESPONSE_REJECTED',
                          'PM_UNAVAILABLE', 'PM_OUTCOME_UNKNOWN')
                dispatch_for_state = {
                    'WAITING_EXECUTOR': 'NOT_STARTED',
                    'VERIFYING_EXECUTOR': 'NOT_STARTED',
                    'WAITING_PM': 'NOT_STARTED', 'PM_DISPATCHING': 'CLAIMED',
                    'PM_RETURNED': 'RETURNED', 'PM_RESPONSE_REJECTED': 'RETURNED',
                    'PM_UNAVAILABLE': 'UNAVAILABLE', 'PM_OUTCOME_UNKNOWN': 'UNKNOWN'}
                if (developer['provider_profile'] != 'opencode' or
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
                if callback['state'] == 'WAITING_PM' and (
                        callback['pm_dispatch'] != 'NOT_STARTED' or
                        callback['pm_response'] is not None or
                        callback['pm_recommendation'] is not None or missing):
                    _fail()
                if callback['state'] in ('WAITING_PM', 'PM_DISPATCHING',
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
                            any(type(test) is not dict or test.get('exit_code') != 0 or
                                type(test.get('exit_code')) is not int or
                                test.get('source_sha') != developer['source_sha'] or
                                test.get('result_head') is not None or
                                test.get('diff_sha256') != result['diff_sha256'] or
                                test.get('path') not in developer['read_paths'] +
                                    developer['write_paths'] or
                                type(test.get('output_sha256')) is not str or
                                re.fullmatch(r'[0-9a-f]{64}', test['output_sha256']) is None
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
                        if type(usage) is not dict or set(usage) != _USAGE_FIELDS:
                            _fail()
                        validate_provider_usage(usage)
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
                            if type(usage) is not dict or set(usage) != _USAGE_FIELDS:
                                _fail()
                            validate_provider_usage(usage)
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
                    'WAITING_REVIEW', 'WAITING_CI', 'WAITING_PM',
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
                    'WAITING_PM', 'PM_DISPATCHING', 'PM_RETURNED', 'PM_RESPONSE_REJECTED',
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
                        any(type(test) is not dict or test.get('source_sha') != result_head or
                            type(test.get('exit_code')) is not int or test.get('exit_code') != 0
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
                    if type(completion_usage) is not dict or set(completion_usage) != _USAGE_FIELDS:
                        _fail()
                    validate_provider_usage(completion_usage)
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
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
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
            if len(raw) > MAX_RECORD_BYTES or raw != repeated or _identity(before) != _identity(after) or _identity(before) != _identity(current):
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
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
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
        if type(record) is not dict or 'dispatch_audit' in record:
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

    def _update(self, task_id, *, expected, changes, trusted_dispatch_audit=False):
        if 'dispatch_audit' in changes and not trusted_dispatch_audit:
            _fail()
        directory = self._dirfd()
        lock = '.' + self._name(task_id) + '.lock'
        locked = False
        try:
            try:
                lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o600, dir_fd=directory)
            except FileExistsError:
                if expected is not None:
                    raise TaskStoreConflict(
                        'developer assignment already exists; duplicate dispatch refused') from None
                _fail('TASK_STORE_BUSY')
            os.close(lock_fd)
            locked = True
            current = self._read_at(directory, task_id)
            if expected is not None and any(current.get(key) != value
                                            for key, value in expected.items()):
                raise TaskStoreConflict(
                    'developer assignment already exists; duplicate dispatch refused')
            from . import worker_dispatch
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
            if locked:
                try:
                    os.unlink(lock, dir_fd=directory)
                except OSError:
                    pass
            os.close(directory)
