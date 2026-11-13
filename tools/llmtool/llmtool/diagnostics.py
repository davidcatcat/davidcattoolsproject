"""Bounded, immutable diagnostic values; no collection, lifecycle, or I/O.

Callers supply application-controlled messages and explicitly selected metadata.
Validation rejects recognizable credentials; it is not arbitrary secret discovery.
No flag/event authorizes recovery, execution, or a provider request.
"""

import base64
import binascii
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Optional

from .terminal import contains_unsafe_controls
from .test_output_excerpts import _CLOUD_KEY

EVENT_MAX_BYTES = 16 * 1024
DIAGNOSTIC_TEXT_MAX_BYTES = 2 * 1024
ERROR_MESSAGE_MAX_BYTES = 1024
SAFE_CAUSE_MAX_BYTES = 4 * 1024
PROVIDER_METADATA_MAX_BYTES = 4 * 1024
IDENTIFIER_MAX_CHARS = 128
MODEL_MAX_CHARS = 128
PROFILE_MAX_CHARS = 64
MAX_DURATION_MS = 2**31 - 1
MAX_TOKEN_COUNT = 10**12
PROVIDER_USAGE_FIELDS = ('prompt_tokens', 'completion_tokens', 'total_tokens')
MAX_EVENT_ID = 2**63 - 1
MAX_JSON_DEPTH = 3
MAX_JSON_ITEMS = 64
MAX_RUN_EVENTS = 32
MAX_RUN_EVENT_BYTES = 64 * 1024

EVENT_SCHEMA = 'llmtool.diagnostics.event'
EVENT_SCHEMA_VERSION = 1


class DiagnosticSeverity(str, Enum):
    INFO = 'info'
    WARNING = 'warning'
    ERROR = 'error'


class DiagnosticMode(str, Enum):
    NORMAL = 'normal'
    VERBOSE = 'verbose'
    DEBUG = 'debug'


class ErrorCode(str, Enum):
    USER_INPUT_ERROR = 'USER_INPUT_ERROR'
    CONFIG_INVALID = 'CONFIG_INVALID'
    CONFIG_PROVIDER_NOT_READY = 'CONFIG_PROVIDER_NOT_READY'
    COMPANY_GW_DIRECT_RETIRED = 'COMPANY_GW_DIRECT_RETIRED'
    AUTH_CREDENTIAL_MISSING = 'AUTH_CREDENTIAL_MISSING'
    AUTH_PROVIDER_REJECTED = 'AUTH_PROVIDER_REJECTED'
    ENV_PROVIDER_RUNTIME_MISSING = 'ENV_PROVIDER_RUNTIME_MISSING'
    NETWORK_PROVIDER_UNREACHABLE = 'NETWORK_PROVIDER_UNREACHABLE'
    PROVIDER_TIMEOUT = 'PROVIDER_TIMEOUT'
    PROVIDER_RATE_LIMITED = 'PROVIDER_RATE_LIMITED'
    PROVIDER_HTTP_ERROR = 'PROVIDER_HTTP_ERROR'
    PROVIDER_RUNTIME_ERROR = 'PROVIDER_RUNTIME_ERROR'
    MODEL_RESPONSE_INVALID = 'MODEL_RESPONSE_INVALID'
    MODEL_OUTPUT_INCOMPLETE = 'MODEL_OUTPUT_INCOMPLETE'
    MODEL_RESPONSE_TOO_LARGE = 'MODEL_RESPONSE_TOO_LARGE'
    LOCAL_IO_ERROR = 'LOCAL_IO_ERROR'
    INTERNAL_UNEXPECTED = 'INTERNAL_UNEXPECTED'
    DIAGNOSTICS_STORE_UNAVAILABLE = 'DIAGNOSTICS_STORE_UNAVAILABLE'
    DIAGNOSTICS_STORE_UNSAFE = 'DIAGNOSTICS_STORE_UNSAFE'
    DIAGNOSTICS_STORE_FULL = 'DIAGNOSTICS_STORE_FULL'
    DIAGNOSTICS_RUN_COLLISION = 'DIAGNOSTICS_RUN_COLLISION'
    DIAGNOSTICS_WRITE_FAILED = 'DIAGNOSTICS_WRITE_FAILED'
    DIAGNOSTICS_EVENT_DROPPED = 'DIAGNOSTICS_EVENT_DROPPED'


class EventType(str, Enum):
    RUN_START = 'run.start'
    RUN_END = 'run.end'
    PROVIDER_REQUEST = 'provider.request'
    PROVIDER_RESPONSE = 'provider.response'
    WARNING = 'warning'
    ERROR = 'error'


# Fixed application categories, never exception text or model-generated prose.
_CAUSE_CATEGORIES = frozenset((
    'input', 'configuration', 'credential', 'http', 'runtime', 'connection',
    'timeout', 'invalid_response', 'incomplete', 'overflow', 'local_io',
    'unexpected', 'unavailable', 'dropped',
))
_PROVIDER_TYPES = frozenset(('openai', 'openai-compatible', 'claude-cli', 'codex-cli'))
_RUN_STATUSES = frozenset(('success', 'error', 'interrupted', 'output_closed', 'incomplete'))
# Recognizable credential values, not ordinary authorization/status vocabulary.
_CREDENTIAL = re.compile(
    r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|'
    + _CLOUD_KEY + r'|(?<!(?-i:[A-Za-z0-9_]))sk-[A-Za-z0-9_-]{12,}|'
    r'(?<!(?-i:[A-Za-z0-9_]))(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|'
    r'(?<!(?-i:[A-Za-z0-9_]))xox[abprs]-[A-Za-z0-9-]{20,}|'
    r'(?<!(?-i:[A-Za-z0-9_]))eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?!(?-i:[A-Za-z0-9_]))', re.IGNORECASE,
)
_AUTH_VALUE = re.compile(
    r'(?<!(?-i:[A-Za-z0-9_]))(bearer|basic)\s+([A-Za-z0-9._~+/-]+={0,2})',
    re.IGNORECASE,
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r'["\']?(?<!(?-i:[A-Za-z0-9_]))(?:api[_-]?key|password|token|secret|access[_-]?token|client[_-]?secret|'
    r'aws_secret_access_key)["\']?\s*[:=]\s*(<redacted>|\[redacted\]|"[^"\n]*"|\'[^\'\n]*\'|[^\s,;}\]]+)',
    re.IGNORECASE,
)
_CREDENTIAL_STATUSES = frozenset((
    'missing', 'unset', 'redacted', '<redacted>', '[redacted]', 'none', 'null',
    'not configured', 'placeholder',
))
_IDENTIFIER = re.compile(r'[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*')
_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/@-]*')
_EVENT_SEVERITIES = {
    EventType.ERROR: DiagnosticSeverity.ERROR,
    EventType.WARNING: DiagnosticSeverity.WARNING,
    EventType.RUN_START: DiagnosticSeverity.INFO,
    EventType.PROVIDER_REQUEST: DiagnosticSeverity.INFO,
    EventType.PROVIDER_RESPONSE: DiagnosticSeverity.INFO,
}
_MAPPING_TYPES = (dict, MappingProxyType)


def _contains_credential_value(value):
    if _CREDENTIAL.search(value):
        return True
    for match in _AUTH_VALUE.finditer(value):
        scheme, token = match.groups()
        if len(token) >= 8 and token.lower() not in _CREDENTIAL_STATUSES:
            if scheme.lower() == 'basic':
                try:
                    decoded = base64.b64decode(token, validate=True)
                except (ValueError, binascii.Error):
                    continue
                if b':' not in decoded:
                    continue  # Ordinary words after Basic are not credentials.
            return True
    for match in _CREDENTIAL_ASSIGNMENT.finditer(value):
        token = match.group(1).strip('"\'').lower()
        if token in _CREDENTIAL_STATUSES or not token or set(token) == {'*'}:
            continue
        if token == 'not' and re.match(r'\s+configured\b', value[match.end():], re.IGNORECASE):
            continue
        return True
    return False


def safe_diagnostic_text(value, max_bytes=DIAGNOSTIC_TEXT_MAX_BYTES):
    """Validate single-line UTF-8 text; reject, never echo or stringify bad input."""
    if type(max_bytes) is not int or not 1 <= max_bytes <= DIAGNOSTIC_TEXT_MAX_BYTES:
        raise ValueError('invalid diagnostic text bound')
    if type(value) is not str or not value.strip() or len(value) > max_bytes:
        raise ValueError('diagnostic text must be bounded nonempty text')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError:
        raise ValueError('diagnostic text must be valid UTF-8') from None
    if size > max_bytes:
        raise ValueError('diagnostic text exceeds byte bound')
    if contains_unsafe_controls(value) or any(ord(c) < 32 for c in value):
        raise ValueError('diagnostic text contains unsupported controls')
    if _contains_credential_value(value):
        raise ValueError('diagnostic text contains recognizable credential material')
    return value


def _identifier(value):
    safe_diagnostic_text(value, IDENTIFIER_MAX_CHARS)
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError('invalid diagnostic component identifier')


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('diagnostic integer is outside supported bounds')


def validate_provider_usage(value):
    """Detach/freeze one complete numeric usage triplet; never accept raw JSON.

    Unknown fields, partial usage and inconsistent totals are rejected. Reuse
    diagnostic integer/byte bounds; callers may drop metadata without changing
    the primary provider failure. No missing value is estimated or filled in.
    """
    if (type(value) not in _MAPPING_TYPES or len(value) != len(PROVIDER_USAGE_FIELDS)
            or any(type(key) is not str for key in value)
            or set(value) != set(PROVIDER_USAGE_FIELDS)):
        raise ValueError('provider usage requires exactly the numeric triplet')
    result = {name: value[name] for name in PROVIDER_USAGE_FIELDS}
    for count in result.values():
        _integer(count, 0, MAX_TOKEN_COUNT)
    if result['total_tokens'] != result['prompt_tokens'] + result['completion_tokens']:
        raise ValueError('provider usage total is inconsistent')
    diagnostic_json(result, PROVIDER_METADATA_MAX_BYTES)
    return MappingProxyType(result)


def validate_run_id(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{32}', value) is None:
        raise ValueError('run identifier must be 32 lowercase hexadecimal characters')
    return value


def validate_span_id(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{16}', value) is None:
        raise ValueError('span identifier must be 16 lowercase hexadecimal characters')
    return value


def new_run_id():
    return validate_run_id(secrets.token_hex(16))


def new_span_id():
    return validate_span_id(secrets.token_hex(8))


def utc_timestamp(when=None):
    """UTC RFC3339 milliseconds. Wall time is not used to calculate duration."""
    if when is None:
        when = datetime.now(timezone.utc)
    if type(when) is not datetime or when.tzinfo is None or when.utcoffset() is None:
        raise ValueError('diagnostic timestamp requires an aware datetime')
    return when.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def validate_timestamp(value):
    if type(value) is not str or re.fullmatch(
            r'[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z', value) is None:
        raise ValueError('diagnostic timestamp requires UTC RFC3339 milliseconds')
    try:
        datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError:
        raise ValueError('diagnostic timestamp has an invalid calendar value') from None
    return value


def diagnostic_json(data, max_bytes=EVENT_MAX_BYTES):
    """Encode bounded plain JSON values only. This helper does not collect data.

    Model-specific field allowlists are enforced before calling this encoder.
    Floats (including NaN/Infinity), arbitrary mappings/objects and arrays are not
    needed by v1 diagnostics and are rejected. Never use default=str.
    """
    _integer(max_bytes, 1, EVENT_MAX_BYTES)
    count = 0

    def plain(value, depth):
        nonlocal count
        count += 1
        if count > MAX_JSON_ITEMS or depth > MAX_JSON_DEPTH:
            raise ValueError('diagnostic JSON exceeds structural bounds')
        if type(value) in _MAPPING_TYPES:
            if len(value) > MAX_JSON_ITEMS:
                raise ValueError('diagnostic JSON exceeds structural bounds')
            result = {}
            for key, item in value.items():
                safe_diagnostic_text(key, IDENTIFIER_MAX_CHARS)
                result[key] = plain(item, depth + 1)
            return result
        if type(value) is str:
            return safe_diagnostic_text(value)
        if value is None or type(value) is bool:
            return value
        if type(value) is int:
            _integer(value, -MAX_EVENT_ID, MAX_EVENT_ID)
            return value
        raise ValueError('diagnostic JSON requires supported plain values')

    if type(data) not in _MAPPING_TYPES:
        raise ValueError('diagnostic JSON requires a plain mapping')
    result = json.dumps(plain(data, 0), ensure_ascii=True, allow_nan=False,
                        sort_keys=True, separators=(',', ':'))
    if len(result.encode('utf-8')) > max_bytes:
        raise ValueError('diagnostic JSON exceeds byte bound')
    return result


def _safe_cause(value):
    if type(value) not in _MAPPING_TYPES or len(value) > 3:
        raise ValueError('diagnostic cause requires a closed scalar mapping')
    result = dict(value)
    for key, item in result.items():
        if type(key) is not str:
            raise ValueError('invalid diagnostic cause field')
        if key == 'http_status':
            _integer(item, 100, 599)
        elif key == 'process_status':
            _integer(item, -(2**31), 2**32 - 1)
        elif key == 'category':
            if type(item) is not str or item not in _CAUSE_CATEGORIES:
                raise ValueError('unsupported diagnostic cause category')
        else:
            raise ValueError('unsupported diagnostic cause field')
    diagnostic_json(result, SAFE_CAUSE_MAX_BYTES)
    return MappingProxyType(result)


@dataclass(frozen=True)
class DiagnosticError:
    code: ErrorCode
    message: str
    component: str
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    retryable: bool = False
    recoverable: bool = False
    fallback_allowed: bool = False
    user_action_required: bool = False
    safe_cause: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self):
        if type(self.code) is not ErrorCode or type(self.severity) is not DiagnosticSeverity:
            raise ValueError('diagnostic code and severity require supported enum values')
        safe_diagnostic_text(self.message, ERROR_MESSAGE_MAX_BYTES)
        _identifier(self.component)
        for name in ('retryable', 'recoverable', 'fallback_allowed', 'user_action_required'):
            if type(getattr(self, name)) is not bool:
                raise ValueError('diagnostic flags must be booleans')
        if self.fallback_allowed:
            raise ValueError('A11a does not permit fallback')
        object.__setattr__(self, 'safe_cause', _safe_cause(self.safe_cause))
        self.to_json()

    def to_dict(self):
        return {'code': self.code.value, 'message': self.message, 'component': self.component,
                'severity': self.severity.value, 'retryable': self.retryable,
                'recoverable': self.recoverable, 'fallback_allowed': self.fallback_allowed,
                'user_action_required': self.user_action_required, 'safe_cause': dict(self.safe_cause)}

    def to_json(self):
        return diagnostic_json(self.to_dict(), EVENT_MAX_BYTES)


def _provider_data(data):
    for key, value in data.items():
        if key == 'provider_type':
            if type(value) is not str or value not in _PROVIDER_TYPES:
                raise ValueError('unsupported diagnostic provider type')
        elif key == 'profile':
            safe_diagnostic_text(value, PROFILE_MAX_CHARS)
            if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', value) is None:
                raise ValueError('invalid diagnostic profile identifier')
        elif key == 'model':
            safe_diagnostic_text(value, MODEL_MAX_CHARS)
            if _MODEL.fullmatch(value) is None or any(part in ('.', '..') for part in value.split('/')):
                raise ValueError('invalid diagnostic model identifier')
        elif key == 'duration_ms':
            _integer(value, 0, MAX_DURATION_MS)
        elif key == 'token_usage_available':
            if type(value) is not bool:
                raise ValueError('diagnostic availability must be boolean')
        else:
            # Only token-count names reach this helper from the event allowlist.
            if value is not None:
                _integer(value, 0, MAX_TOKEN_COUNT)
    diagnostic_json(data, PROVIDER_METADATA_MAX_BYTES)


def _event_data(kind, value, component, severity):
    if type(value) not in _MAPPING_TYPES or len(value) > MAX_JSON_ITEMS:
        raise ValueError('event data requires a bounded plain allowlisted mapping')
    data = dict(value)
    if any(type(k) is not str for k in data):
        raise ValueError('invalid diagnostic event field')
    if kind in (EventType.ERROR, EventType.WARNING):
        allowed = {'code', 'message', 'retryable', 'recoverable', 'fallback_allowed',
                   'user_action_required', 'safe_cause'}
        if set(data) - allowed:
            raise ValueError('unsupported diagnostic event field')
        if not {'code', 'message'} <= set(data):
            raise ValueError('diagnostic error event requires code and message')
        if type(data['code']) is not str:
            raise ValueError('diagnostic event code must be supported text')
        try:
            code = ErrorCode(data['code'])
        except ValueError:
            raise ValueError('unsupported diagnostic event code') from None
        error = DiagnosticError(component=component, severity=severity, **{**data, 'code': code})
        data = {k: v for k, v in error.to_dict().items() if k not in ('component', 'severity')}
    elif kind in (EventType.PROVIDER_REQUEST, EventType.PROVIDER_RESPONSE):
        allowed = {'provider_type', 'profile', 'model'}
        if kind == EventType.PROVIDER_RESPONSE:
            allowed |= {'duration_ms', 'input_tokens', 'output_tokens', 'prompt_tokens',
                        'completion_tokens', 'total_tokens', 'token_usage_available'}
        if set(data) - allowed:
            raise ValueError('unsupported provider event field')
        _provider_data(data)
    else:
        allowed = {'command', 'mode'} if kind == EventType.RUN_START else {'status', 'duration_ms'}
        if set(data) - allowed:
            raise ValueError('unsupported run event field')
        for key, item in data.items():
            if key == 'command' and (type(item) is not str or item != 'ask'):
                raise ValueError('unsupported diagnostic command')
            if key == 'mode' and (type(item) is not str or item not in {m.value for m in DiagnosticMode}):
                raise ValueError('unsupported diagnostic mode')
            if key == 'status' and (type(item) is not str or item not in _RUN_STATUSES):
                raise ValueError('unsupported diagnostic run status')
            if key == 'duration_ms':
                _integer(item, 0, MAX_DURATION_MS)
    # Detach and freeze the sole supported nested mapping as well.
    if 'safe_cause' in data:
        data['safe_cause'] = _safe_cause(data['safe_cause'])
    return MappingProxyType(data)


@dataclass(frozen=True)
class DiagnosticEvent:
    run_id: str
    event_id: int
    timestamp: str
    type: EventType
    component: str
    severity: DiagnosticSeverity
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    data: Mapping[str, object] = field(default_factory=dict)
    schema: str = EVENT_SCHEMA
    schema_version: int = EVENT_SCHEMA_VERSION

    def __post_init__(self):
        if type(self.schema) is not str or self.schema != EVENT_SCHEMA:
            raise ValueError('unsupported diagnostic event schema')
        if type(self.schema_version) is not int or self.schema_version != EVENT_SCHEMA_VERSION:
            raise ValueError('unsupported diagnostic event schema version')
        validate_run_id(self.run_id)
        _integer(self.event_id, 1, MAX_EVENT_ID)
        validate_timestamp(self.timestamp)
        if type(self.type) is not EventType or type(self.severity) is not DiagnosticSeverity:
            raise ValueError('event type and severity require supported enum values')
        expected_severity = _EVENT_SEVERITIES.get(self.type)
        if expected_severity is not None and self.severity is not expected_severity:
            raise ValueError('diagnostic event severity does not match its type')
        # diagnostics_runtime owns the deterministic ask run.end severity policy.
        _identifier(self.component)
        for value in (self.span_id, self.parent_span_id):
            if value is not None:
                validate_span_id(value)
        if self.parent_span_id is not None and (self.span_id is None or self.span_id == self.parent_span_id):
            raise ValueError('event parent requires a distinct span')
        object.__setattr__(self, 'data', _event_data(self.type, self.data, self.component, self.severity))
        self.to_json()

    def to_dict(self):
        data = {k: dict(v) if type(v) is MappingProxyType else v for k, v in self.data.items()}
        return {'schema': self.schema, 'schema_version': self.schema_version,
                'run_id': self.run_id, 'event_id': self.event_id, 'timestamp': self.timestamp,
                'type': self.type.value, 'component': self.component, 'severity': self.severity.value,
                'span_id': self.span_id, 'parent_span_id': self.parent_span_id, 'data': data}

    def to_json(self):
        return diagnostic_json(self.to_dict(), EVENT_MAX_BYTES)
