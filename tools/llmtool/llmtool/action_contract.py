"""Immutable proposal/receipt contracts, not an executor or approval issuer.

All state is caller-supplied. No filesystem, Git, provider, clock, or policy is
consulted. TrustedApprovalContext is an embedding caller's attestation precondition,
not authentication. JSON source='USER', parsing, and a nonce confer no authority.
"""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, fields
from enum import Enum
from typing import Optional, Union

from .errors import LLMToolError

JSON_LIMIT = 64 * 1024
MAX_DEPTH = 6
MAX_VALUES = 1024
MAX_PATH_BYTES = 1024
MAX_PATH_COMPONENTS = 32
MAX_FILES = 32
MAX_CONTENT_BYTES = 1024 * 1024
MAX_INTEGER = 2**63 - 1
MAX_EPOCH = 253402300799
MAX_APPROVAL_SECONDS = 3600
MAX_CONSUMED_NONCES = 128


def _invalid():
    raise LLMToolError('action contract invalid; details omitted')


def _integer(value, maximum=MAX_INTEGER, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        _invalid()
    return value


def _text(value, limit=MAX_PATH_BYTES):
    if type(value) is not str or not value or len(value) > limit:
        _invalid()
    try:
        if len(value.encode('utf-8')) > limit:
            _invalid()
    except UnicodeError:
        _invalid()
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 or unicodedata.category(c) == 'Cf'
           for c in value):
        _invalid()
    return value


def _hex(value, lengths=(64,)):
    _text(value, max(lengths))
    if len(value) not in lengths or re.fullmatch('[0-9a-f]+', value) is None:
        _invalid()
    return value


def _identifier(value):
    _text(value, 64)
    if re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', value) is None:
        _invalid()
    return value


# Conservative literal path vocabulary; no shell syntax, globs, expansion or
# hidden credential/Git storage. Ordinary Unicode letters/digits are preserved.
_DENIED_PARTS = frozenset(('.git', '.ssh', '.aws', '.kube', '.codex', '.claude',
    '.gnupg', '.azure', '.docker', '.password-store', '.env', '.netrc', '.npmrc',
    '.pypirc', '.pgpass', '.git-credentials', '.vault-token', 'id_rsa', 'id_dsa',
    'id_ecdsa', 'id_ed25519', 'credentials.json', 'kubeconfig'))


def _path(value, absolute=False):
    _text(value)
    if value.startswith('/') != absolute or value.endswith('/') or value == '/':
        _invalid()
    parts = value[1:].split('/') if absolute else value.split('/')
    if not 1 <= len(parts) <= MAX_PATH_COMPONENTS:
        _invalid()
    for part in parts:
        lower = part.lower()
        if (not part or part in ('.', '..') or part.startswith('-') or
                lower in _DENIED_PARTS or lower.startswith('.env.') or
                lower.endswith(('.pem', '.key', '.p12', '.pfx')) or
                any(not (c.isalnum() or c in '._-') for c in part)):
            _invalid()
    if any(parts[i].lower() == '.config' and parts[i+1].lower() in
           ('llmtool', 'gh', 'gcloud') for i in range(len(parts)-1)):
        _invalid()
    return value


def _collection(value, item_type, maximum=MAX_FILES, minimum=1):
    if type(value) not in (list, tuple) or not minimum <= len(value) <= maximum:
        _invalid()
    if any(type(item) is not item_type for item in value):
        _invalid()
    return tuple(value)


def _scope(value):
    value = _collection(value, str)
    for path in value:
        _path(path)
    if tuple(sorted(set(value))) != value:
        _invalid()
    return value


def _typed(value, expected):
    if type(value) is not expected:
        _invalid()


def _version(value):
    if type(value) is not int or value != 1:
        _invalid()


def _data(value):
    if isinstance(value, ContractValue):
        return {f.name: _data(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if type(value) is tuple:
        return [_data(v) for v in value]
    return value


def _canonical(value):
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError):
        _invalid()
    if len(raw) > JSON_LIMIT:
        _invalid()
    return raw


def _digest(domain, value):
    # Exact digest bytes: compact sorted UTF-8 JSON of this fixed envelope.
    return hashlib.sha256(_canonical({'domain': domain, 'version': 1,
                                      'value': value.to_dict()})).hexdigest()


class ContractValue:
    def to_dict(self):
        return _data(self)

    def to_json(self):
        return _canonical(self.to_dict()).decode('utf-8')


class ActionKind(str, Enum):
    FILE_CREATE = 'FILE_CREATE'
    FILE_WRITE = 'FILE_WRITE'
    FILE_DELETE = 'FILE_DELETE'
    GIT_OPERATION = 'GIT_OPERATION'


class FileType(str, Enum):
    ABSENT = 'ABSENT'
    REGULAR = 'REGULAR'


class GitOperation(str, Enum):
    ADD = 'ADD'


@dataclass(frozen=True)
class TaskBinding(ContractValue):
    task_id: str
    run_id: str
    contract_version: int
    contract_digest: str

    def __post_init__(self):
        _identifier(self.task_id)
        _hex(self.run_id, (32,))
        _integer(self.contract_version, maximum=2**31-1, minimum=1)
        _hex(self.contract_digest)


@dataclass(frozen=True)
class RepositoryBinding(ContractValue):
    repository_id: str
    canonical_root: str
    root_device: int
    root_inode: int
    expected_head: str

    def __post_init__(self):
        _hex(self.repository_id)
        _path(self.canonical_root, absolute=True)
        _integer(self.root_device)
        _integer(self.root_inode, minimum=1)
        _hex(self.expected_head, (40, 64))


@dataclass(frozen=True)
class FileMetadata(ContractValue):
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    size: int
    mtime_ns: int
    nlink: int

    def __post_init__(self):
        for name in ('device', 'uid', 'gid', 'mtime_ns'):
            _integer(getattr(self, name))
        _integer(self.inode, minimum=1)
        _integer(self.mode, maximum=0o777)
        _integer(self.size, maximum=MAX_CONTENT_BYTES)
        if type(self.nlink) is not int or self.nlink != 1:
            _invalid()


@dataclass(frozen=True)
class FileState(ContractValue):
    exists: bool
    file_type: FileType
    content_sha256: Optional[str]
    metadata: Optional[FileMetadata]

    def __post_init__(self):
        _typed(self.exists, bool)
        _typed(self.file_type, FileType)
        if self.file_type is FileType.ABSENT:
            if self.exists or self.content_sha256 is not None or self.metadata is not None:
                _invalid()
        else:
            if not self.exists:
                _invalid()
            _hex(self.content_sha256)
            _typed(self.metadata, FileMetadata)


@dataclass(frozen=True)
class FileExpectation(ContractValue):
    path: str
    state: FileState

    def __post_init__(self):
        _path(self.path)
        _typed(self.state, FileState)


def _files(value):
    value = _collection(value, FileExpectation)
    paths = tuple(item.path for item in value)
    if tuple(sorted(set(paths))) != paths:
        _invalid()
    if sum(item.state.metadata.size for item in value if item.state.exists) > MAX_CONTENT_BYTES:
        _invalid()
    return value


@dataclass(frozen=True)
class RepositoryState(ContractValue):
    version: int
    repository: RepositoryBinding
    files: tuple[FileExpectation, ...]
    index_sha256: Optional[str]

    def __post_init__(self):
        _version(self.version)
        _typed(self.repository, RepositoryBinding)
        object.__setattr__(self, 'files', _files(self.files))
        if self.index_sha256 is not None:
            _hex(self.index_sha256)
        self.to_json()

    @property
    def digest(self):
        return _digest('llmtool.action-state', self)


@dataclass(frozen=True)
class FileChangePayload(ContractValue):
    target: FileExpectation
    new_content_sha256: str
    new_size: int
    new_mode: int

    def __post_init__(self):
        _typed(self.target, FileExpectation)
        _hex(self.new_content_sha256)
        _integer(self.new_size, maximum=MAX_CONTENT_BYTES)
        _integer(self.new_mode, maximum=0o777)


@dataclass(frozen=True)
class FileDeletePayload(ContractValue):
    target: FileExpectation

    def __post_init__(self):
        _typed(self.target, FileExpectation)
        if not self.target.state.exists:
            _invalid()


@dataclass(frozen=True)
class GitAddPayload(ContractValue):
    operation: GitOperation
    files: tuple[FileExpectation, ...]
    expected_index_sha256: str

    def __post_init__(self):
        _typed(self.operation, GitOperation)
        object.__setattr__(self, 'files', _files(self.files))
        if any(not f.state.exists for f in self.files):
            _invalid()
        _hex(self.expected_index_sha256)


@dataclass(frozen=True)
class Action(ContractValue):
    schema_version: int
    action_id: str
    created_at: int
    kind: ActionKind
    task: TaskBinding
    repository: RepositoryBinding
    scope: tuple[str, ...]
    payload: Union[FileChangePayload, FileDeletePayload, GitAddPayload]

    def __post_init__(self):
        _version(self.schema_version)
        _identifier(self.action_id)
        _integer(self.created_at, maximum=MAX_EPOCH)
        _typed(self.kind, ActionKind)
        _typed(self.task, TaskBinding)
        _typed(self.repository, RepositoryBinding)
        object.__setattr__(self, 'scope', _scope(self.scope))
        payload_type = {ActionKind.FILE_CREATE: FileChangePayload,
                        ActionKind.FILE_WRITE: FileChangePayload,
                        ActionKind.FILE_DELETE: FileDeletePayload,
                        ActionKind.GIT_OPERATION: GitAddPayload}[self.kind]
        _typed(self.payload, payload_type)
        if self.kind in (ActionKind.FILE_CREATE, ActionKind.FILE_WRITE):
            if self.payload.target.state.exists != (self.kind is ActionKind.FILE_WRITE):
                _invalid()
        if any(f.path not in self.scope for f in self.expected_state.files):
            _invalid()
        self.to_json()

    @property
    def expected_state(self):
        if self.kind is ActionKind.GIT_OPERATION:
            return RepositoryState(1, self.repository, self.payload.files,
                                   self.payload.expected_index_sha256)
        return RepositoryState(1, self.repository, (self.payload.target,), None)

    @property
    def digest(self):
        return _digest('llmtool.action', self)


@dataclass(frozen=True)
class Approval(ContractValue):
    schema_version: int
    source: str
    action_digest: str
    action_kind: ActionKind
    task: TaskBinding
    repository: RepositoryBinding
    scope: tuple[str, ...]
    state_digest: str
    issued_at: int
    expires_at: int
    nonce: str

    def __post_init__(self):
        _version(self.schema_version)
        if type(self.source) is not str or self.source != 'USER':
            _invalid()
        _hex(self.action_digest)
        _typed(self.action_kind, ActionKind)
        _typed(self.task, TaskBinding)
        _typed(self.repository, RepositoryBinding)
        object.__setattr__(self, 'scope', _scope(self.scope))
        _hex(self.state_digest)
        _integer(self.issued_at, maximum=MAX_EPOCH)
        _integer(self.expires_at, maximum=MAX_EPOCH)
        if not 1 <= self.expires_at - self.issued_at <= MAX_APPROVAL_SECONDS:
            _invalid()
        _hex(self.nonce)
        self.to_json()

    @property
    def digest(self):
        return _digest('llmtool.approval', self)


@dataclass(frozen=True)
class TrustedApprovalContext(ContractValue):
    """Caller attestation only; NEVER construct from model/JSON/CLI input.

    An embedding host must authenticate the actual user's exact receipt decision
    before supplying this object. This module cannot verify that precondition.
    No parser, issuer, signature verifier or execution authority is implemented.
    """
    approval_digest: str

    def __post_init__(self):
        _hex(self.approval_digest)


class ApprovalCode(str, Enum):
    BINDING_MISMATCH = 'BINDING_MISMATCH'
    STATE_MISMATCH = 'STATE_MISMATCH'
    NOT_YET_VALID = 'NOT_YET_VALID'
    EXPIRED = 'EXPIRED'
    NONCE_MISMATCH = 'NONCE_MISMATCH'
    NONCE_CONSUMED = 'NONCE_CONSUMED'
    TRUST_MISMATCH = 'TRUST_MISMATCH'
    NOT_AUTHORIZED = 'NOT_AUTHORIZED'
    USER_ATTESTED = 'USER_ATTESTED'


@dataclass(frozen=True)
class ApprovalValidation(ContractValue):
    bindings_valid: bool
    trusted_user_source: bool
    code: ApprovalCode

    def __post_init__(self):
        _typed(self.bindings_valid, bool)
        _typed(self.trusted_user_source, bool)
        _typed(self.code, ApprovalCode)
        if self.bindings_valid != (self.code in (ApprovalCode.NOT_AUTHORIZED, ApprovalCode.USER_ATTESTED)):
            _invalid()
        if self.trusted_user_source != (self.code is ApprovalCode.USER_ATTESTED):
            _invalid()

    @property
    def authorized(self):
        return False

    def to_dict(self):
        return {**super().to_dict(), 'authorized': False}


def validate_approval(action, approval, *, current_state, expected_nonce, now,
                      consumed_nonces=(), trusted_context=None):
    """Validate supplied bindings, not authentic issuance or execution permission.

    current_state/nonce/time are synthetic caller inputs; consumed_nonces is a
    bounded synthetic check, NOT a persistent replay ledger or atomic consumption.
    Future execution needs independently trusted state, fresh rechecks, an issuer,
    durable replay/audit and sandbox/policy. Even USER_ATTESTED is authorized=False.
    """
    _typed(action, Action)
    _typed(approval, Approval)
    _typed(current_state, RepositoryState)
    _hex(expected_nonce)
    _integer(now, maximum=MAX_EPOCH)
    consumed_nonces = _collection(consumed_nonces, str, MAX_CONSUMED_NONCES, 0)
    for nonce in consumed_nonces:
        _hex(nonce)
    if len(set(consumed_nonces)) != len(consumed_nonces):
        _invalid()
    if trusted_context is not None:
        _typed(trusted_context, TrustedApprovalContext)
    checks = (
        (approval.action_digest != action.digest or approval.action_kind is not action.kind or
         approval.task != action.task or approval.repository != action.repository or
         approval.scope != action.scope or approval.state_digest != action.expected_state.digest or
         approval.issued_at < action.created_at, ApprovalCode.BINDING_MISMATCH),
        (current_state != action.expected_state, ApprovalCode.STATE_MISMATCH),
        (now < approval.issued_at, ApprovalCode.NOT_YET_VALID),
        (now >= approval.expires_at, ApprovalCode.EXPIRED),
        (approval.nonce != expected_nonce, ApprovalCode.NONCE_MISMATCH),
        (approval.nonce in consumed_nonces, ApprovalCode.NONCE_CONSUMED),
        (trusted_context is not None and trusted_context.approval_digest != approval.digest,
         ApprovalCode.TRUST_MISMATCH),
    )
    for failed, code in checks:
        if failed:
            return ApprovalValidation(False, False, code)
    trusted = trusted_context is not None
    return ApprovalValidation(True, trusted,
        ApprovalCode.USER_ATTESTED if trusted else ApprovalCode.NOT_AUTHORIZED)


class AuditType(str, Enum):
    PROPOSED = 'PROPOSED'
    GRANTED = 'GRANTED'
    REJECTED = 'REJECTED'
    EXPIRED = 'EXPIRED'
    INVALID_CONTRACT = 'INVALID_CONTRACT'


@dataclass(frozen=True)
class AuditEvent(ContractValue):
    """Safe metadata only; not persisted, authenticated or durable audit evidence."""
    version: int
    event_type: AuditType
    action_digest: Optional[str]
    approval_digest: Optional[str]
    action_kind: Optional[ActionKind]
    code: Optional[ApprovalCode]

    def __post_init__(self):
        _version(self.version)
        _typed(self.event_type, AuditType)
        for value in (self.action_digest, self.approval_digest):
            if value is not None:
                _hex(value)
        if self.action_kind is not None:
            _typed(self.action_kind, ActionKind)
        if self.code is not None:
            _typed(self.code, ApprovalCode)
        if self.event_type is AuditType.INVALID_CONTRACT:
            if any(value is not None for value in
                   (self.action_digest, self.approval_digest, self.action_kind, self.code)):
                _invalid()
        else:
            if self.action_digest is None or self.action_kind is None:
                _invalid()
            if self.event_type is AuditType.PROPOSED:
                if not ((self.approval_digest is None and self.code is None) or
                        (self.approval_digest is not None and self.code is ApprovalCode.NOT_AUTHORIZED)):
                    _invalid()
            elif self.approval_digest is None or self.code is None:
                _invalid()
            elif self.event_type is AuditType.GRANTED:
                if self.code is not ApprovalCode.USER_ATTESTED:
                    _invalid()
            elif self.event_type is AuditType.EXPIRED:
                if self.code is not ApprovalCode.EXPIRED:
                    _invalid()
            elif self.code in (ApprovalCode.USER_ATTESTED, ApprovalCode.NOT_AUTHORIZED, ApprovalCode.EXPIRED):
                _invalid()
        self.to_json()


def proposal_audit(action):
    _typed(action, Action)
    return AuditEvent(1, AuditType.PROPOSED, action.digest, None, action.kind, None)


def approval_audit(action, approval, validation):
    _typed(action, Action)
    _typed(approval, Approval)
    _typed(validation, ApprovalValidation)
    event = (AuditType.GRANTED if validation.trusted_user_source else
             AuditType.PROPOSED if validation.bindings_valid else
             AuditType.EXPIRED if validation.code is ApprovalCode.EXPIRED else AuditType.REJECTED)
    return AuditEvent(1, event, action.digest, approval.digest, action.kind, validation.code)


def _object(value, names):
    if type(value) is not dict or set(value) != set(names):
        _invalid()
    return value


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _invalid()
        value[key] = item
    return value


def _load(text):
    if type(text) is not str:
        _invalid()
    try:
        if len(text) > JSON_LIMIT or len(text.encode('utf-8')) > JSON_LIMIT:
            _invalid()
    except UnicodeError:
        _invalid()
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            if depth > MAX_DEPTH:
                _invalid()
        elif char in ']}':
            depth -= 1
    try:
        value = json.loads(text, object_pairs_hook=_pairs,
                           parse_constant=lambda _: _invalid())
    except (ValueError, RecursionError):
        _invalid()
    count = 0
    def walk(item):
        nonlocal count
        count += 1
        if count > MAX_VALUES:
            _invalid()
        if type(item) is dict:
            for key, child in item.items():
                _text(key)
                walk(child)
        elif type(item) is list:
            for child in item:
                walk(child)
        elif type(item) is str:
            _text(item)
        elif item is not None and type(item) not in (int, bool):
            _invalid()
    walk(value)
    return value


def _construct(model, value):
    return model(**_object(value, (f.name for f in fields(model))))


def _enum(model, value):
    if type(value) is not str:
        _invalid()
    try:
        return model(value)
    except ValueError:
        _invalid()


def _task(value):
    return _construct(TaskBinding, value)


def _repository(value):
    return _construct(RepositoryBinding, value)


def _expectation(value):
    value = _object(value, ('path', 'state'))
    state = _object(value['state'], ('exists', 'file_type', 'content_sha256', 'metadata'))
    metadata = None if state['metadata'] is None else _construct(FileMetadata, state['metadata'])
    return FileExpectation(value['path'], FileState(state['exists'],
        _enum(FileType, state['file_type']), state['content_sha256'], metadata))


def _expectations(value):
    if type(value) is not list or not 1 <= len(value) <= MAX_FILES:
        _invalid()
    return tuple(_expectation(item) for item in value)


def parse_repository_state(text):
    value = _object(_load(text), ('version', 'repository', 'files', 'index_sha256'))
    return RepositoryState(value['version'], _repository(value['repository']),
                           _expectations(value['files']), value['index_sha256'])


def parse_action(text):
    value = _object(_load(text), (f.name for f in fields(Action)))
    kind = _enum(ActionKind, value['kind'])
    payload = value['payload']
    if kind in (ActionKind.FILE_CREATE, ActionKind.FILE_WRITE):
        payload = _object(payload, (f.name for f in fields(FileChangePayload)))
        payload = FileChangePayload(_expectation(payload['target']),
            payload['new_content_sha256'], payload['new_size'], payload['new_mode'])
    elif kind is ActionKind.FILE_DELETE:
        payload = _object(payload, ('target',))
        payload = FileDeletePayload(_expectation(payload['target']))
    else:
        payload = _object(payload, (f.name for f in fields(GitAddPayload)))
        payload = GitAddPayload(_enum(GitOperation, payload['operation']),
            _expectations(payload['files']), payload['expected_index_sha256'])
    if type(value['scope']) is not list:
        _invalid()
    return Action(value['schema_version'], value['action_id'], value['created_at'], kind,
        _task(value['task']), _repository(value['repository']), value['scope'], payload)


def parse_approval(text):
    value = _object(_load(text), (f.name for f in fields(Approval)))
    if type(value['scope']) is not list:
        _invalid()
    return Approval(value['schema_version'], value['source'], value['action_digest'],
        _enum(ActionKind, value['action_kind']), _task(value['task']),
        _repository(value['repository']), value['scope'], value['state_digest'],
        value['issued_at'], value['expires_at'], value['nonce'])
