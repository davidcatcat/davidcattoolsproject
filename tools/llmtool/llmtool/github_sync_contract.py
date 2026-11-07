"""Parse and validate GitHubSync messages read from GitHub comments (#1144 WP5).

This module is pure. It makes no network call, writes no file or Task, and
posts nothing. A parsed message is a request record only: ``authority`` never
returns an execution grant, from either trusted login, and a GitHub record is
never Owner execution authority.

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

NO_ACTION = 'NO_ACTION'
RECORD_ONLY = 'RECORD_ONLY'
REQUEST_FOR_PM = 'REQUEST_FOR_PM'
STOP_REQUEST_FOR_PM = 'STOP_REQUEST_FOR_PM'
AUTHORITY_DECISIONS = frozenset({NO_ACTION, RECORD_ONLY, REQUEST_FOR_PM,
                                 STOP_REQUEST_FOR_PM})
EXECUTE_AUTHORITY_NOT_ESTABLISHED = 'EXECUTE_AUTHORITY_NOT_ESTABLISHED'

_COMMON_FIELDS = frozenset({'schema_version', 'kind', 'request_id', 'revision'})
_TEXT_FIELDS = frozenset({'title', 'summary'})
# kind -> (required fields, optional fields)
_FIELDS = {
    DISCUSS: (_COMMON_FIELDS | _TEXT_FIELDS, frozenset()),
    RECORD: (_COMMON_FIELDS | _TEXT_FIELDS, frozenset()),
    EXECUTE_REQUEST: (_COMMON_FIELDS | _TEXT_FIELDS | {'scope'}, frozenset()),
    STOP_REQUEST: (_COMMON_FIELDS | _TEXT_FIELDS | {'task_id'}, frozenset()),
    ACK: (_COMMON_FIELDS | {'status'}, frozenset({'task_id', 'reason_code'})),
}

_REQUEST_ID = re.compile(r'[a-z0-9][a-z0-9-]{7,63}')
_TASK_ID = re.compile(r'[0-9a-f]{32}')
_REASON_CODE = re.compile(r'[A-Z][A-Z0-9_]{0,63}')
_TIMESTAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z')
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
    task_id: Optional[str] = None
    status: Optional[str] = None
    reason_code: Optional[str] = None


@dataclass(frozen=True)
class AuthorityDecision:
    """The fixed decision for one message. No value is an execution grant."""

    decision: str
    reason: Optional[str] = None
    task_id: Optional[str] = None
    request_key: Optional[str] = None
    delivery_id: Optional[str] = None


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
        task_id=values.get('task_id'),
        status=values.get('status'),
        reason_code=values.get('reason_code'),
    )


def authority(message):
    """Return the fixed decision for ``message``; never an execution grant."""
    if type(message) is not SyncMessage:
        return AuthorityDecision(NO_ACTION, NOT_A_SYNC_MESSAGE)
    bound = {'request_key': message.request_key, 'delivery_id': message.delivery_id}
    if message.kind == STOP_REQUEST:
        if not _valid_task_id(message.task_id):
            return AuthorityDecision(NO_ACTION, SYNC_FIELD_INVALID, **bound)
        return AuthorityDecision(STOP_REQUEST_FOR_PM, None, task_id=message.task_id,
                                 **bound)
    if message.kind == EXECUTE_REQUEST:
        return AuthorityDecision(REQUEST_FOR_PM, EXECUTE_AUTHORITY_NOT_ESTABLISHED,
                                 **bound)
    if message.kind == RECORD:
        return AuthorityDecision(RECORD_ONLY, None, **bound)
    # DISCUSS, ACK and any forged kind.
    return AuthorityDecision(NO_ACTION, None, **bound)


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
        if 'task_id' in envelope:
            if not _valid_task_id(envelope['task_id']):
                raise _Refused(SYNC_FIELD_INVALID)
            values['task_id'] = envelope['task_id']
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
        if kind == STOP_REQUEST:
            if not _valid_task_id(envelope['task_id']):
                raise _Refused(SYNC_FIELD_INVALID)
            values['task_id'] = envelope['task_id']
    # Screen only after every structural check has passed.
    if any(_sensitive(value) for value in screened):
        raise _Refused(SYNC_SENSITIVE_CONTENT)
    return values


def _trusted_issuer(user):
    login = user.get('login') if type(user) is dict else None
    trusted = (github_identity.owner_login(), github_identity.ai_login())
    if type(login) is not str or login not in trusted:
        raise _Refused(SYNC_ISSUER_UNTRUSTED)
    return login


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


def _valid_reason_code(value):
    return type(value) is str and _REASON_CODE.fullmatch(value) is not None
