"""Parse and validate GitHubSync messages read from GitHub comments (#1144 WP5).

This module is pure. It makes no network call, writes no file or Task, posts
nothing, executes nothing and dispatches nothing. A parsed message is a request
record only, and a GitHub login or comment text is never Owner authority.

``authority`` returns a class for the PM (Owner Phase A, #1144). For an
EXECUTE_REQUEST it compares the message with an Owner authorization record the
PM built from an existing Owner decision; it never creates, widens or stores
one. ``DISPATCH_NORMAL_FLOW`` means only that the request lies inside that
record. The PM still runs every normal gate (dispatch gate, task scope, review,
CI) before any work starts.

A message is exactly one fenced code block whose info string is
``llmtool-sync`` holding one JSON object. Comments without such a block are
ignored (``NOT_A_SYNC_MESSAGE``). Refusals carry one fixed code and never echo
field values. Trust is decided by the exact ``user.login`` only; the comment's
``author_association`` and ``html_url`` are not used.

Inbound kinds are DISCUSS, RECORD, EXECUTE_REQUEST and STOP_REQUEST. The ACK
kind is the outbound acknowledgement written by ``render_ack``; the same
parser reads it back so the PM's own acknowledgements are recognized, and
``authority`` gives it NO_ACTION.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
from typing import Optional, Tuple

from .errors import LLMToolError
from . import github_identity
from .task_submission_cli import _screen_text


SYNC_INFO = 'llmtool-sync'
SCHEMA_VERSION = 1
MAX_ENVELOPE_BYTES = 8192
MAX_TITLE_CHARS = 200
MAX_SUMMARY_CHARS = 4000
MAX_SCOPE_PATHS = 50
MAX_SCOPE_PATH_BYTES = 256
MIN_REVISION = 1
MAX_REVISION = 1000
DELIVERY_DOMAIN = 'llmtool-github-sync-delivery-v1'
ACK_HEADING = 'llmtool-sync acknowledgement'

DISCUSS = 'DISCUSS'
RECORD = 'RECORD'
EXECUTE_REQUEST = 'EXECUTE_REQUEST'
STOP_REQUEST = 'STOP_REQUEST'
ACK = 'ACK'
INBOUND_KINDS = frozenset({DISCUSS, RECORD, EXECUTE_REQUEST, STOP_REQUEST})

ACK_STATUSES = frozenset({'RECEIVED', 'REFUSED', 'LINKED', 'WAITING_OWNER',
                          'BLOCKED', 'DONE'})

NOT_A_SYNC_MESSAGE = 'NOT_A_SYNC_MESSAGE'
SYNC_MULTIPLE_ENVELOPES = 'SYNC_MULTIPLE_ENVELOPES'
SYNC_ENVELOPE_TOO_LARGE = 'SYNC_ENVELOPE_TOO_LARGE'
SYNC_JSON_INVALID = 'SYNC_JSON_INVALID'
SYNC_SCHEMA_UNSUPPORTED = 'SYNC_SCHEMA_UNSUPPORTED'
SYNC_KIND_UNKNOWN = 'SYNC_KIND_UNKNOWN'
SYNC_FIELD_MISSING = 'SYNC_FIELD_MISSING'
SYNC_FIELD_UNKNOWN = 'SYNC_FIELD_UNKNOWN'
SYNC_FIELD_INVALID = 'SYNC_FIELD_INVALID'
SYNC_SENSITIVE_CONTENT = 'SYNC_SENSITIVE_CONTENT'
SYNC_ISSUER_UNTRUSTED = 'SYNC_ISSUER_UNTRUSTED'
SYNC_EDITED = 'SYNC_EDITED'
REFUSAL_CODES = frozenset({
    SYNC_MULTIPLE_ENVELOPES, SYNC_ENVELOPE_TOO_LARGE, SYNC_JSON_INVALID,
    SYNC_SCHEMA_UNSUPPORTED, SYNC_KIND_UNKNOWN, SYNC_FIELD_MISSING,
    SYNC_FIELD_UNKNOWN, SYNC_FIELD_INVALID, SYNC_SENSITIVE_CONTENT,
    SYNC_ISSUER_UNTRUSTED, SYNC_EDITED,
})

OPERATIONS = frozenset({'DEVELOP', 'DOCS', 'TEST_ONLY'})

LEDGER_NEW = 'NEW'
LEDGER_DUPLICATE = 'DUPLICATE'
LEDGER_STATUSES = frozenset({LEDGER_NEW, LEDGER_DUPLICATE})

# Decision classes. None of them executes anything.
NO_ACTION = 'NO_ACTION'
RECORD_ONLY = 'RECORD_ONLY'
STOP_REQUEST_FOR_PM = 'STOP_REQUEST_FOR_PM'
NEVER_REDISPATCH = 'NEVER_REDISPATCH'
WAITING_OWNER = 'WAITING_OWNER'
REFUSED_OUT_OF_SCOPE = 'REFUSED_OUT_OF_SCOPE'
DISPATCH_NORMAL_FLOW = 'DISPATCH_NORMAL_FLOW'
DECISION_CLASSES = frozenset({
    NO_ACTION, RECORD_ONLY, STOP_REQUEST_FOR_PM, NEVER_REDISPATCH, WAITING_OWNER,
    REFUSED_OUT_OF_SCOPE, DISPATCH_NORMAL_FLOW,
})

# Decision reason codes.
REASON_NOT_A_SYNC_MESSAGE = NOT_A_SYNC_MESSAGE
REASON_MESSAGE_INVALID = 'MESSAGE_INVALID'
REASON_DISCUSS = 'DISCUSS'
REASON_RECORD = 'RECORD'
REASON_ACK = 'ACK'
REASON_STOP_REQUEST = 'STOP_REQUEST'
REASON_DUPLICATE = 'DUPLICATE'
REASON_NO_AUTHORIZATION = 'NO_AUTHORIZATION'
REASON_AUTHORIZATION_INVALID = 'AUTHORIZATION_INVALID'
REASON_REVOKED = 'REVOKED'
REASON_EXPIRED = 'EXPIRED'
REASON_ISSUE_MISMATCH = 'ISSUE_MISMATCH'
REASON_OPERATION_OUT_OF_SCOPE = 'OPERATION_OUT_OF_SCOPE'
REASON_TASK_OUT_OF_SCOPE = 'TASK_OUT_OF_SCOPE'
REASON_PATH_OUT_OF_SCOPE = 'PATH_OUT_OF_SCOPE'
REASON_AUTHORIZED = 'AUTHORIZED'
AUTHORITY_REASONS = frozenset({
    REASON_NOT_A_SYNC_MESSAGE, REASON_MESSAGE_INVALID, REASON_DISCUSS, REASON_RECORD,
    REASON_ACK, REASON_STOP_REQUEST, REASON_DUPLICATE, REASON_NO_AUTHORIZATION,
    REASON_AUTHORIZATION_INVALID, REASON_REVOKED, REASON_EXPIRED, REASON_ISSUE_MISMATCH,
    REASON_OPERATION_OUT_OF_SCOPE, REASON_TASK_OUT_OF_SCOPE, REASON_PATH_OUT_OF_SCOPE,
    REASON_AUTHORIZED,
})

_COMMON_FIELDS = frozenset({'schema_version', 'kind', 'request_id', 'revision'})
_TEXT_FIELDS = frozenset({'title', 'summary'})
# kind -> (required fields, optional fields)
_FIELDS = {
    DISCUSS: (_COMMON_FIELDS | _TEXT_FIELDS, frozenset()),
    RECORD: (_COMMON_FIELDS | _TEXT_FIELDS, frozenset()),
    EXECUTE_REQUEST: (_COMMON_FIELDS | _TEXT_FIELDS | {'scope', 'operation'},
                      frozenset({'task_id'})),
    STOP_REQUEST: (_COMMON_FIELDS | _TEXT_FIELDS | {'task_id'}, frozenset()),
    ACK: (_COMMON_FIELDS | {'status'}, frozenset({'task_id', 'reason_code'})),
}
_AUTHORIZATION_FIELDS = frozenset({'owner_ref', 'issue_number', 'task_id', 'operations',
                                   'scope', 'expires_at', 'revoked'})

_REQUEST_ID = re.compile(r'[a-z0-9][a-z0-9-]{7,63}')
_TASK_ID = re.compile(r'[0-9a-f]{32}')
_REASON_CODE = re.compile(r'[A-Z][A-Z0-9_]{0,63}')
_TIMESTAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z')
_TIMESTAMP_FORMAT = '%Y-%m-%dT%H:%M:%SZ'
_OWNER_REF = re.compile(
    r'https://github\.com/[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?'
    r'/[A-Za-z0-9][A-Za-z0-9._-]{0,99}/issues/[1-9][0-9]{0,6}'
    r'#issuecomment-[1-9][0-9]{0,18}')
_FENCE = re.compile(r' {0,3}(`{3,}|~{3,})(.*)')
_DRIVE = re.compile(r'[A-Za-z]:')
_MAX_ISSUE_NUMBER = 9999999
_MAX_COMMENT_ID = 2 ** 63 - 1
_SCREEN_LIMIT = 16 * 1024


class GitHubSyncError(LLMToolError):
    """An invalid caller argument; the message is the fixed code only."""

    def __init__(self, code):
        if code not in REFUSAL_CODES:
            raise ValueError('unknown GitHubSync error code')
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SyncRefusal:
    """A refused or ignored comment. Holds the fixed code and nothing else."""

    code: str

    def __post_init__(self):
        if self.code != NOT_A_SYNC_MESSAGE and self.code not in REFUSAL_CODES:
            raise ValueError('unknown GitHubSync refusal code')

    @property
    def ignored(self):
        """True when the comment holds no sync block; not an error."""
        return self.code == NOT_A_SYNC_MESSAGE


@dataclass(frozen=True)
class SyncMessage:
    """One validated GitHubSync message. It is a request record, not authority."""

    kind: str
    request_id: str
    revision: int
    issuer_login: str
    issue_number: int
    comment_id: int
    request_key: str
    delivery_id: str
    title: Optional[str] = field(default=None, repr=False)
    summary: Optional[str] = field(default=None, repr=False)
    scope: Optional[Tuple[str, ...]] = None
    operation: Optional[str] = None
    task_id: Optional[str] = None
    status: Optional[str] = None
    reason_code: Optional[str] = None


@dataclass(frozen=True)
class AuthorityDecision:
    """The class for one message, for the PM. It executes nothing.

    ``owner_ref`` is set only for ``DISPATCH_NORMAL_FLOW``. ``task_id`` is the
    STOP_REQUEST target or the authorized Task of a dispatchable request.
    """

    decision_class: str
    reason_code: str
    owner_ref: Optional[str] = None
    task_id: Optional[str] = None
    request_key: Optional[str] = None
    delivery_id: Optional[str] = None

    def __post_init__(self):
        if (type(self.decision_class) is not str
                or self.decision_class not in DECISION_CLASSES
                or type(self.reason_code) is not str
                or self.reason_code not in AUTHORITY_REASONS):
            raise ValueError('unknown GitHubSync decision')
        dispatchable = self.decision_class == DISPATCH_NORMAL_FLOW
        if dispatchable != (self.owner_ref is not None):
            raise ValueError('owner_ref belongs to DISPATCH_NORMAL_FLOW only')


@dataclass(frozen=True)
class _Authorization:
    owner_ref: str
    issue_number: int
    task_id: Optional[str]
    operations: frozenset
    prefixes: Tuple[Tuple[str, ...], ...]
    expires_at: datetime
    revoked: bool


class _Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _DuplicateKey(ValueError):
    pass


def parse_comment(comment, issue_number):
    """Return a ``SyncMessage`` or a ``SyncRefusal`` for one REST API comment.

    Raises ``GitHubSyncError`` only when the caller passes a non-dict comment
    or an invalid issue number.
    """
    if type(comment) is not dict or not _valid_issue_number(issue_number):
        raise GitHubSyncError(SYNC_FIELD_INVALID)
    body = comment.get('body')
    blocks = _sync_blocks(body) if type(body) is str else []
    if not blocks:
        return SyncRefusal(NOT_A_SYNC_MESSAGE)
    try:
        # Issuer and edit state are checked before any content is parsed, so
        # untrusted or changed text is never interpreted.
        login = _trusted_issuer(comment.get('user'))
        comment_id = comment.get('id')
        created = comment.get('created_at')
        updated = comment.get('updated_at')
        if (type(comment_id) is not int or not 1 <= comment_id <= _MAX_COMMENT_ID
                or not _valid_timestamp(created) or not _valid_timestamp(updated)):
            raise _Refused(SYNC_FIELD_INVALID)
        if updated != created:
            raise _Refused(SYNC_EDITED)
        if len(blocks) > 1:
            raise _Refused(SYNC_MULTIPLE_ENVELOPES)
        content, closed = blocks[0]
        values = _validate(_load_envelope(content, closed))
    except _Refused as refused:
        return SyncRefusal(refused.code)
    return SyncMessage(
        kind=values['kind'],
        request_id=values['request_id'],
        revision=values['revision'],
        issuer_login=login,
        issue_number=issue_number,
        comment_id=comment_id,
        request_key=f'{login}:{values["request_id"]}',
        delivery_id=_delivery_id(issue_number, comment_id, values['request_id'],
                                 values['revision']),
        title=values.get('title'),
        summary=values.get('summary'),
        scope=values.get('scope'),
        operation=values.get('operation'),
        task_id=values.get('task_id'),
        status=values.get('status'),
        reason_code=values.get('reason_code'),
    )


def authority(message, authorization=None, ledger_status=LEDGER_NEW, now=None):
    """Return the Phase A ``AuthorityDecision`` for ``message``.

    ``authorization`` is a dict the PM built from an existing Owner
    authorization record; it is only compared, never changed or stored.
    ``ledger_status`` is ``NEW`` or ``DUPLICATE`` from the PM's delivery
    ledger. ``now`` is a timezone-aware datetime (default: current UTC time).
    Raises ``GitHubSyncError`` only for an invalid ``ledger_status`` or ``now``.
    """
    if type(ledger_status) is not str or ledger_status not in LEDGER_STATUSES:
        raise GitHubSyncError(SYNC_FIELD_INVALID)
    current = _current_time(now)
    if type(message) is not SyncMessage:
        return AuthorityDecision(NO_ACTION, REASON_NOT_A_SYNC_MESSAGE)
    # A SyncMessage can be built by hand; re-check what the decision relies on.
    if not _message_intact(message):
        return AuthorityDecision(NO_ACTION, REASON_MESSAGE_INVALID)
    bound = {'request_key': message.request_key, 'delivery_id': message.delivery_id}
    if message.kind == DISCUSS:
        return AuthorityDecision(NO_ACTION, REASON_DISCUSS, **bound)
    if message.kind == RECORD:
        return AuthorityDecision(RECORD_ONLY, REASON_RECORD, **bound)
    if message.kind == ACK:
        return AuthorityDecision(NO_ACTION, REASON_ACK, **bound)
    if message.kind == STOP_REQUEST:
        return AuthorityDecision(STOP_REQUEST_FOR_PM, REASON_STOP_REQUEST,
                                 task_id=message.task_id, **bound)
    return _execute_decision(message, authorization, ledger_status, current, bound)


def _execute_decision(message, authorization, ledger_status, current, bound):
    """Apply the Phase A order for one intact EXECUTE_REQUEST."""
    if ledger_status == LEDGER_DUPLICATE:
        return AuthorityDecision(NEVER_REDISPATCH, REASON_DUPLICATE, **bound)
    if authorization is None:
        return AuthorityDecision(WAITING_OWNER, REASON_NO_AUTHORIZATION, **bound)
    record = _read_authorization(authorization)
    if record is None:
        return AuthorityDecision(WAITING_OWNER, REASON_AUTHORIZATION_INVALID, **bound)
    if record.revoked:
        return AuthorityDecision(NEVER_REDISPATCH, REASON_REVOKED, **bound)
    if current >= record.expires_at:
        return AuthorityDecision(NEVER_REDISPATCH, REASON_EXPIRED, **bound)
    if record.issue_number != message.issue_number:
        return AuthorityDecision(WAITING_OWNER, REASON_ISSUE_MISMATCH, **bound)
    if message.operation not in record.operations:
        return AuthorityDecision(REFUSED_OUT_OF_SCOPE, REASON_OPERATION_OUT_OF_SCOPE,
                                 **bound)
    if message.task_id != record.task_id:
        return AuthorityDecision(REFUSED_OUT_OF_SCOPE, REASON_TASK_OUT_OF_SCOPE, **bound)
    if not all(_within_any(_path_parts(path), record.prefixes) for path in message.scope):
        return AuthorityDecision(REFUSED_OUT_OF_SCOPE, REASON_PATH_OUT_OF_SCOPE, **bound)
    return AuthorityDecision(DISPATCH_NORMAL_FLOW, REASON_AUTHORIZED,
                             owner_ref=record.owner_ref, task_id=message.task_id, **bound)


def _current_time(now):
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise GitHubSyncError(SYNC_FIELD_INVALID)
    return now.astimezone(timezone.utc)


def _message_intact(message):
    """True when a SyncMessage still satisfies the parser's invariants."""
    kind = message.kind
    if (type(kind) is not str or kind not in _FIELDS
            or not _valid_request_id(message.request_id)
            or not _valid_revision(message.revision)
            or not _valid_issue_number(message.issue_number)
            or type(message.comment_id) is not int
            or not 1 <= message.comment_id <= _MAX_COMMENT_ID
            or type(message.issuer_login) is not str
            or message.issuer_login not in _trusted_logins()):
        return False
    if message.request_key != f'{message.issuer_login}:{message.request_id}':
        return False
    if message.delivery_id != _delivery_id(message.issue_number, message.comment_id,
                                           message.request_id, message.revision):
        return False
    if kind == STOP_REQUEST:
        return _valid_task_id(message.task_id)
    if kind == EXECUTE_REQUEST:
        scope = message.scope
        return (_valid_operation(message.operation)
                and (message.task_id is None or _valid_task_id(message.task_id))
                and type(scope) is tuple and 1 <= len(scope) <= MAX_SCOPE_PATHS
                and all(_valid_scope_path(path) for path in scope))
    return True


def _read_authorization(value):
    """Return a private snapshot of a well-formed authorization, else None."""
    if type(value) is not dict or set(value) != _AUTHORIZATION_FIELDS:
        return None
    owner_ref = value['owner_ref']
    if type(owner_ref) is not str or _OWNER_REF.fullmatch(owner_ref) is None:
        return None
    issue_number = value['issue_number']
    if not _valid_issue_number(issue_number):
        return None
    task_id = value['task_id']
    if task_id is not None and not _valid_task_id(task_id):
        return None
    operations = value['operations']
    if (type(operations) not in (list, tuple) or not operations
            or not all(_valid_operation(item) for item in operations)):
        return None
    scope = value['scope']
    if type(scope) not in (list, tuple) or not scope:
        return None
    prefixes = []
    for prefix in scope:
        parts = _path_parts(prefix) if _valid_scope_path(prefix) else ()
        if not parts:
            return None  # An authorization must name a path, never the whole tree.
        prefixes.append(parts)
    expires_at = _parse_timestamp(value['expires_at'])
    revoked = value['revoked']
    if expires_at is None or type(revoked) is not bool:
        return None
    return _Authorization(owner_ref=owner_ref, issue_number=issue_number, task_id=task_id,
                          operations=frozenset(operations), prefixes=tuple(prefixes),
                          expires_at=expires_at, revoked=revoked)


def _parse_timestamp(value):
    if not _valid_timestamp(value):
        return None
    try:
        return datetime.strptime(value, _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _path_parts(path):
    """Split a validated repo-relative path into components, dropping '' and '.'."""
    return tuple(part for part in path.split('/') if part not in ('', '.'))


def _within_any(parts, prefixes):
    # Compare whole components, so 'tools/llmtool-extra' is not inside 'tools/llmtool'.
    return any(len(parts) >= len(prefix) and parts[:len(prefix)] == prefix
               for prefix in prefixes)


def render_ack(request_id, revision, status, task_id=None, reason_code=None):
    """Return a comment body with one ACK block. Holds no inbound free text."""
    if (not _valid_request_id(request_id) or not _valid_revision(revision)
            or type(status) is not str or status not in ACK_STATUSES
            or (task_id is not None and not _valid_task_id(task_id))
            or (reason_code is not None and not _valid_reason_code(reason_code))):
        raise GitHubSyncError(SYNC_FIELD_INVALID)
    if _sensitive(request_id):
        raise GitHubSyncError(SYNC_SENSITIVE_CONTENT)
    payload = {'schema_version': SCHEMA_VERSION, 'kind': ACK,
               'request_id': request_id, 'revision': revision, 'status': status}
    if task_id is not None:
        payload['task_id'] = task_id
    if reason_code is not None:
        payload['reason_code'] = reason_code
    text = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return f'{ACK_HEADING}\n\n```{SYNC_INFO}\n{text}\n```\n'


def _sync_blocks(body):
    """Return ``(content, closed)`` for each top-level llmtool-sync fence."""
    lines = body.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    blocks = []
    fence = None  # (fence char, fence length, is sync, content lines)
    for line in lines:
        match = _FENCE.match(line)
        if fence is None:
            if match is None:
                continue
            marker, info = match.group(1), match.group(2).strip(' \t')
            if marker[0] == '`' and '`' in info:
                continue  # Not a fence in CommonMark.
            fence = (marker[0], len(marker), info == SYNC_INFO, [])
            continue
        char, length, is_sync, content = fence
        if (match is not None and match.group(1)[0] == char
                and len(match.group(1)) >= length
                and not match.group(2).strip(' \t')):
            if is_sync:
                blocks.append(('\n'.join(content), True))
            fence = None
        elif is_sync:
            content.append(line)
    if fence is not None and fence[2]:
        blocks.append(('\n'.join(fence[3]), False))
    return blocks


def _load_envelope(content, closed):
    if len(content.encode('utf-8', 'surrogatepass')) > MAX_ENVELOPE_BYTES:
        raise _Refused(SYNC_ENVELOPE_TOO_LARGE)
    try:
        content.encode('utf-8')
    except UnicodeError:
        raise _Refused(SYNC_JSON_INVALID) from None
    if not closed:
        raise _Refused(SYNC_JSON_INVALID)
    try:
        value = json.loads(content, object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise _Refused(SYNC_JSON_INVALID) from None
    if type(value) is not dict:
        raise _Refused(SYNC_JSON_INVALID)
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey('duplicate JSON key')
        result[key] = value
    return result


def _reject_constant(_name):
    raise ValueError('non-finite JSON number')


def _validate(envelope):
    if 'schema_version' not in envelope:
        raise _Refused(SYNC_FIELD_MISSING)
    version = envelope['schema_version']
    if type(version) is not int or version != SCHEMA_VERSION:
        raise _Refused(SYNC_SCHEMA_UNSUPPORTED)
    if 'kind' not in envelope:
        raise _Refused(SYNC_FIELD_MISSING)
    kind = envelope['kind']
    if type(kind) is not str or kind not in _FIELDS:
        raise _Refused(SYNC_KIND_UNKNOWN)
    required, optional = _FIELDS[kind]
    if any(key not in required and key not in optional for key in envelope):
        raise _Refused(SYNC_FIELD_UNKNOWN)
    if any(key not in envelope for key in required):
        raise _Refused(SYNC_FIELD_MISSING)

    values = {'kind': kind, 'request_id': envelope['request_id'],
              'revision': envelope['revision']}
    if not _valid_request_id(values['request_id']) or not _valid_revision(values['revision']):
        raise _Refused(SYNC_FIELD_INVALID)
    screened = [values['request_id']]
    if kind == ACK:
        status = envelope['status']
        if type(status) is not str or status not in ACK_STATUSES:
            raise _Refused(SYNC_FIELD_INVALID)
        values['status'] = status
        if 'reason_code' in envelope:
            if not _valid_reason_code(envelope['reason_code']):
                raise _Refused(SYNC_FIELD_INVALID)
            values['reason_code'] = envelope['reason_code']
    else:
        title, summary = envelope['title'], envelope['summary']
        if (not _plain_text(title, MAX_TITLE_CHARS, multiline=False)
                or not _plain_text(summary, MAX_SUMMARY_CHARS, multiline=True)):
            raise _Refused(SYNC_FIELD_INVALID)
        values['title'], values['summary'] = title, summary
        screened += [title, summary]
        if kind == EXECUTE_REQUEST:
            scope = envelope['scope']
            if (type(scope) is not list or not 1 <= len(scope) <= MAX_SCOPE_PATHS
                    or not all(_valid_scope_path(path) for path in scope)):
                raise _Refused(SYNC_FIELD_INVALID)
            values['scope'] = tuple(scope)
            screened += scope
            if not _valid_operation(envelope['operation']):
                raise _Refused(SYNC_FIELD_INVALID)
            values['operation'] = envelope['operation']
    # Required for STOP_REQUEST, optional for EXECUTE_REQUEST and ACK.
    if 'task_id' in envelope:
        if not _valid_task_id(envelope['task_id']):
            raise _Refused(SYNC_FIELD_INVALID)
        values['task_id'] = envelope['task_id']
    # Screen only after every structural check has passed.
    if any(_sensitive(value) for value in screened):
        raise _Refused(SYNC_SENSITIVE_CONTENT)
    return values


def _trusted_issuer(user):
    login = user.get('login') if type(user) is dict else None
    if type(login) is not str or login not in _trusted_logins():
        raise _Refused(SYNC_ISSUER_UNTRUSTED)
    return login


def _trusted_logins():
    return (github_identity.owner_login(), github_identity.ai_login())


def _delivery_id(issue_number, comment_id, request_id, revision):
    text = '\0'.join((DELIVERY_DOMAIN, str(issue_number), str(comment_id),
                      request_id, str(revision)))
    return hashlib.sha256(text.encode('ascii')).hexdigest()


def _sensitive(value):
    """Use the record-pm screen; any screen failure counts as sensitive."""
    try:
        _screen_text(value, _SCREEN_LIMIT)
    except Exception:
        return True
    return False


def _plain_text(value, limit, *, multiline):
    if type(value) is not str or not value.strip() or len(value) > limit:
        return False
    try:
        value.encode('utf-8')
    except UnicodeError:
        return False
    allowed = '\t\n\r' if multiline else ''
    for char in value:
        code = ord(char)
        if (code < 32 and char not in allowed) or 127 <= code <= 159:
            return False
        category = unicodedata.category(char)
        if category == 'Cf' or (not multiline and category in ('Zl', 'Zp')):
            return False
    return True


def _valid_scope_path(path):
    if (type(path) is not str or path.startswith('/') or _DRIVE.match(path)
            or '..' in path or '\\' in path or '\0' in path):
        return False
    if not _plain_text(path, MAX_SCOPE_PATH_BYTES, multiline=False):
        return False
    return len(path.encode('utf-8')) <= MAX_SCOPE_PATH_BYTES


def _valid_issue_number(value):
    return type(value) is int and 1 <= value <= _MAX_ISSUE_NUMBER


def _valid_timestamp(value):
    return type(value) is str and _TIMESTAMP.fullmatch(value) is not None


def _valid_request_id(value):
    return type(value) is str and _REQUEST_ID.fullmatch(value) is not None


def _valid_revision(value):
    return type(value) is int and MIN_REVISION <= value <= MAX_REVISION


def _valid_task_id(value):
    return type(value) is str and _TASK_ID.fullmatch(value) is not None


def _valid_operation(value):
    return type(value) is str and value in OPERATIONS


def _valid_reason_code(value):
    return type(value) is str and _REASON_CODE.fullmatch(value) is not None
