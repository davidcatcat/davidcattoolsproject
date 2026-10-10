"""A11b.3 D1 offline authorization decisions; no issuer, effect or parser.

decide() returns DENY, WAITING_APPROVAL or ALLOW_WITH_TRUSTED_RECEIPT for one
synthetic FILE_CREATE. ALLOW needs a host-injected HostIssuer that vouches for
the exact immutable PolicyContract digest; this module defines only that
abstract boundary and ships no concrete issuer, JSON parser or CLI route. An
ALLOW is a fake-sink grant for tests, never a real repository effect.

Guarantees are data-path and API guarantees: model JSON, source='USER',
trusted=true or a receipt id cannot reach ALLOW. They are not OS isolation:
same-UID Python in this process can construct any object.
"""

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .action_contract import (Action, ActionKind, ContractValue, RepositoryBinding,
                              TaskBinding, _collection, _digest, _hex, _identifier,
                              _integer, _path, _scope, _typed)
from .errors import LLMToolError


class Decision(str, Enum):
    DENY = 'DENY'
    WAITING_APPROVAL = 'WAITING_APPROVAL'
    ALLOW_WITH_TRUSTED_RECEIPT = 'ALLOW_WITH_TRUSTED_RECEIPT'


# Product-facing labels. ALLOW only ever means a fake policy grant in D1.
STATUS = {Decision.DENY: 'DENIED', Decision.WAITING_APPROVAL: 'NEEDS_APPROVAL',
          Decision.ALLOW_WITH_TRUSTED_RECEIPT: 'FAKE_POLICY_ALLOWED'}


class DecisionCode(str, Enum):
    INVALID_REQUEST = 'INVALID_REQUEST'
    SEPARATE_AUTHORIZATION_REQUIRED = 'SEPARATE_AUTHORIZATION_REQUIRED'
    OWNER_P0_REQUIRED = 'OWNER_P0_REQUIRED'
    OWNER_P0_NON_INTERACTIVE = 'OWNER_P0_NON_INTERACTIVE'
    UNSUPPORTED_ACTION = 'UNSUPPORTED_ACTION'
    NO_ISSUER = 'NO_ISSUER'
    UNTRUSTED_ISSUER = 'UNTRUSTED_ISSUER'
    UNTRUSTED_POLICY = 'UNTRUSTED_POLICY'
    POLICY_BINDING_MISMATCH = 'POLICY_BINDING_MISMATCH'
    OUT_OF_CONTRACT = 'OUT_OF_CONTRACT'
    STATE_MISMATCH = 'STATE_MISMATCH'
    POLICY_ALLOWED = 'POLICY_ALLOWED'


class IssuerClass(str, Enum):
    POLICY_CONTRACT = 'POLICY_CONTRACT'
    OWNER_P0 = 'OWNER_P0'


class Requirement(str, Enum):
    """Host classification of what an action would need beyond its contract."""
    # Canonical owner P0 (skills/ai-team-delivery): exactly these three.
    OWNER_P0_PRODUCT_MISMATCH = 'OWNER_P0_PRODUCT_MISMATCH'
    OWNER_P0_ADMIN_PRIVILEGE = 'OWNER_P0_ADMIN_PRIVILEGE'
    OWNER_P0_MACOS_SYSTEM = 'OWNER_P0_MACOS_SYSTEM'
    # Separate authorization boundaries: STOP; not P0 and never implied.
    NEW_COST = 'NEW_COST'
    NEW_DATA_EGRESS = 'NEW_DATA_EGRESS'


OWNER_P0 = frozenset((Requirement.OWNER_P0_PRODUCT_MISMATCH,
                      Requirement.OWNER_P0_ADMIN_PRIVILEGE, Requirement.OWNER_P0_MACOS_SYSTEM))
SEPARATE_AUTHORITY = frozenset((Requirement.NEW_COST, Requirement.NEW_DATA_EGRESS))


class EffectKind(str, Enum):
    FAKE_FILE_CREATE = 'FAKE_FILE_CREATE'


def _kinds(value):
    value = _collection(value, ActionKind, len(ActionKind))
    if tuple(sorted(set(value), key=lambda k: k.value)) != value:
        raise LLMToolError('authorization policy invalid; details omitted')
    return value


@dataclass(frozen=True)
class PolicyContract(ContractValue):
    """Immutable reviewed task policy. Its text grants nothing by itself."""
    policy_id: str
    version: int
    task: TaskBinding
    kinds: tuple
    scope: tuple
    effect_kind: EffectKind

    def __post_init__(self):
        _identifier(self.policy_id)
        _integer(self.version, maximum=2**31-1, minimum=1)
        _typed(self.task, TaskBinding)
        object.__setattr__(self, 'kinds', _kinds(self.kinds))
        object.__setattr__(self, 'scope', _scope(self.scope))
        _typed(self.effect_kind, EffectKind)
        self.to_json()

    @property
    def digest(self):
        return _digest('llmtool.authorization-policy', self)


class HostIssuer(abc.ABC):
    """Trusted-host issuer boundary; deliberately no production subclass.

    A host implementation must independently hold the reviewed policy it
    vouches for. Never build one from model, JSON or CLI input.
    """

    @property
    @abc.abstractmethod
    def issuer_class(self):
        """An IssuerClass member."""

    @property
    @abc.abstractmethod
    def capability_id(self):
        """Identifier of the host capability backing this issuer."""

    @abc.abstractmethod
    def vouches_for(self, policy_digest):
        """Return True only for an independently held, reviewed policy digest."""


@dataclass(frozen=True)
class ObservedState(ContractValue):
    """Host-observed pre-state for a synthetic FILE_CREATE; never model input."""
    repository: RepositoryBinding
    head: str
    index_sha256: str
    worktree_sha256: str
    target_path: str
    target_absent: bool
    parent_device: int
    parent_inode: int

    def __post_init__(self):
        _typed(self.repository, RepositoryBinding)
        _hex(self.head, (40, 64))
        _hex(self.index_sha256)
        _hex(self.worktree_sha256)
        _path(self.target_path)
        _typed(self.target_absent, bool)
        _integer(self.parent_device)
        _integer(self.parent_inode, minimum=1)


@dataclass(frozen=True)
class ReceiptBinding(ContractValue):
    """Every value a receipt is bound to (Issue #97 R3); flat for exact storage."""
    task_id: str
    task_run_id: str
    contract_version: int
    contract_digest: str
    action_digest: str
    action_kind: str
    action_paths: tuple
    content_sha256: str
    repository_id: str
    canonical_root: str
    root_device: int
    root_inode: int
    head: str
    index_sha256: str
    worktree_sha256: str
    policy_id: str
    policy_version: int
    policy_digest: str
    decision: str
    issuer_class: str
    capability_id: str
    redeem_run_id: str
    effect_kind: str
    target_path: str
    target_absent: bool
    parent_device: int
    parent_inode: int

    def __post_init__(self):
        _identifier(self.task_id)
        for name in ('task_run_id', 'redeem_run_id'):
            _hex(getattr(self, name), (32,))
        for name in ('contract_digest', 'action_digest', 'content_sha256', 'repository_id',
                     'index_sha256', 'worktree_sha256', 'policy_digest'):
            _hex(getattr(self, name))
        _hex(self.head, (40, 64))
        for name in ('contract_version', 'policy_version'):
            _integer(getattr(self, name), maximum=2**31-1, minimum=1)
        for name in ('root_device', 'parent_device'):
            _integer(getattr(self, name))
        for name in ('root_inode', 'parent_inode'):
            _integer(getattr(self, name), minimum=1)
        if (self.action_kind != ActionKind.FILE_CREATE.value or
                self.decision != Decision.ALLOW_WITH_TRUSTED_RECEIPT.value or
                self.issuer_class != IssuerClass.POLICY_CONTRACT.value or
                self.effect_kind != EffectKind.FAKE_FILE_CREATE.value or
                self.target_absent is not True):
            raise LLMToolError('receipt binding invalid; details omitted')
        object.__setattr__(self, 'action_paths', _scope(self.action_paths))
        _path(self.canonical_root, absolute=True)
        _path(self.target_path)
        _identifier(self.policy_id)
        _identifier(self.capability_id)
        self.to_json()

    @property
    def digest(self):
        return _digest('llmtool.receipt-binding', self)


# Marks decisions made by decide(); keeps dict/JSON data from posing as one.
# Same-UID Python can still reach it; this is not a security boundary.
_DECIDED = object()


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    code: DecisionCode
    binding: Optional[ReceiptBinding]
    _origin: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if (self._origin is not _DECIDED or type(self.decision) is not Decision or
                type(self.code) is not DecisionCode or
                (self.binding is not None) != (self.decision is Decision.ALLOW_WITH_TRUSTED_RECEIPT)):
            raise LLMToolError('authorization decision invalid; details omitted')
        if self.binding is not None:
            _typed(self.binding, ReceiptBinding)

    @property
    def status(self):
        return STATUS[self.decision]


def _result(decision, code, binding=None):
    return PolicyDecision(decision, code, binding, _DECIDED)


def _deny(code):
    return _result(Decision.DENY, code)


def decide(action, policy, *, issuer, observed, redeem_run_id,
           requirements=frozenset(), interactive=False):
    """Decide one synthetic FILE_CREATE; never performs or schedules an effect.

    observed, requirements and interactive come from the host, not the model.
    """
    try:
        _typed(action, Action)
        _typed(policy, PolicyContract)
        _typed(observed, ObservedState)
        _hex(redeem_run_id, (32,))
        _typed(requirements, frozenset)
        if any(type(item) is not Requirement for item in requirements):
            return _deny(DecisionCode.INVALID_REQUEST)
        _typed(interactive, bool)
    except LLMToolError:
        return _deny(DecisionCode.INVALID_REQUEST)
    if requirements & SEPARATE_AUTHORITY:
        return _deny(DecisionCode.SEPARATE_AUTHORIZATION_REQUIRED)
    if requirements & OWNER_P0:
        # D1 has no authenticated owner channel: a P0 waits, never auto-allows.
        return _result(Decision.WAITING_APPROVAL, DecisionCode.OWNER_P0_REQUIRED if interactive
                       else DecisionCode.OWNER_P0_NON_INTERACTIVE)
    if action.kind is not ActionKind.FILE_CREATE:
        return _deny(DecisionCode.UNSUPPORTED_ACTION)
    if issuer is None:
        return _deny(DecisionCode.NO_ISSUER)
    if not isinstance(issuer, HostIssuer):
        return _deny(DecisionCode.UNTRUSTED_ISSUER)
    try:
        issuer_class, capability_id = issuer.issuer_class, issuer.capability_id
        if issuer_class is not IssuerClass.POLICY_CONTRACT:
            return _deny(DecisionCode.UNTRUSTED_ISSUER)
        _identifier(capability_id)
        vouched = issuer.vouches_for(policy.digest)
    except Exception:
        return _deny(DecisionCode.UNTRUSTED_ISSUER)
    if vouched is not True:
        return _deny(DecisionCode.UNTRUSTED_POLICY)
    if policy.task != action.task:
        return _deny(DecisionCode.POLICY_BINDING_MISMATCH)
    if (action.kind not in policy.kinds or any(p not in policy.scope for p in action.scope) or
            policy.effect_kind is not EffectKind.FAKE_FILE_CREATE):
        return _deny(DecisionCode.OUT_OF_CONTRACT)
    target = action.payload.target
    if (observed.repository != action.repository or
            observed.head != action.repository.expected_head or
            observed.target_path != target.path or observed.target_absent is not True or
            target.state.exists):
        return _deny(DecisionCode.STATE_MISMATCH)
    repository = action.repository
    binding = ReceiptBinding(
        action.task.task_id, action.task.run_id, action.task.contract_version,
        action.task.contract_digest, action.digest, action.kind.value, action.scope,
        action.payload.new_content_sha256, repository.repository_id, repository.canonical_root,
        repository.root_device, repository.root_inode, observed.head, observed.index_sha256,
        observed.worktree_sha256, policy.policy_id, policy.version, policy.digest,
        Decision.ALLOW_WITH_TRUSTED_RECEIPT.value, issuer_class.value, capability_id,
        redeem_run_id, policy.effect_kind.value, target.path, True,
        observed.parent_device, observed.parent_inode)
    return _result(Decision.ALLOW_WITH_TRUSTED_RECEIPT, DecisionCode.POLICY_ALLOWED, binding)
