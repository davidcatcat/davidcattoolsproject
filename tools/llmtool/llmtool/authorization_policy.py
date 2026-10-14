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
    PRODUCTION_REVIEW = 'PRODUCTION_REVIEW'
    NOTIFICATION = 'NOTIFICATION'


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
    PROVIDER_REVIEW_CALL = 'PROVIDER_REVIEW_CALL'
    REVIEW_NOTIFICATION = 'REVIEW_NOTIFICATION'


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
        labels = (self.action_kind, self.decision, self.issuer_class, self.effect_kind)
        if (any(type(label) is not str for label in labels) or
                labels != (ActionKind.FILE_CREATE.value, Decision.ALLOW_WITH_TRUSTED_RECEIPT.value,
                           IssuerClass.POLICY_CONTRACT.value, EffectKind.FAKE_FILE_CREATE.value) or
                self.target_absent is not True):
            raise LLMToolError('receipt binding invalid; details omitted')
        object.__setattr__(self, 'action_paths', _scope(self.action_paths))
        if self.target_path not in self.action_paths:
            raise LLMToolError('receipt binding invalid; details omitted')
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
        # One-shot marker: dataclasses.replace() of a decision cannot re-validate.
        object.__setattr__(self, '_origin', None)

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
    if action.kind is not ActionKind.FILE_CREATE:
        return _deny(DecisionCode.UNSUPPORTED_ACTION)
    if issuer is None:
        return _deny(DecisionCode.NO_ISSUER)
    try:
        if not isinstance(issuer, HostIssuer):
            return _deny(DecisionCode.UNTRUSTED_ISSUER)
        issuer_class, capability_id = issuer.issuer_class, issuer.capability_id
        if issuer_class is not IssuerClass.POLICY_CONTRACT:
            return _deny(DecisionCode.UNTRUSTED_ISSUER)
        _identifier(capability_id)
        policy_digest = policy.digest
        vouched = issuer.vouches_for(policy_digest)
    except Exception:
        return _deny(DecisionCode.UNTRUSTED_ISSUER)
    if vouched is not True:
        return _deny(DecisionCode.UNTRUSTED_POLICY)
    if policy.task != action.task:
        return _deny(DecisionCode.POLICY_BINDING_MISMATCH)
    if (action.kind not in policy.kinds or any(p not in policy.scope for p in action.scope) or
            policy.effect_kind is not EffectKind.FAKE_FILE_CREATE):
        return _deny(DecisionCode.OUT_OF_CONTRACT)
    # Only an action already inside a host-vouched contract reaches the owner:
    # untrusted, missing or out-of-contract authority is denied, not escalated.
    # D1 has no authenticated owner channel, so a P0 waits and never auto-allows.
    if requirements & OWNER_P0:
        return _result(Decision.WAITING_APPROVAL, DecisionCode.OWNER_P0_REQUIRED if interactive
                       else DecisionCode.OWNER_P0_NON_INTERACTIVE)
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
        observed.worktree_sha256, policy.policy_id, policy.version, policy_digest,
        Decision.ALLOW_WITH_TRUSTED_RECEIPT.value, issuer_class.value, capability_id,
        redeem_run_id, policy.effect_kind.value, target.path, True,
        observed.parent_device, observed.parent_inode)
    return _result(Decision.ALLOW_WITH_TRUSTED_RECEIPT, DecisionCode.POLICY_ALLOWED, binding)


@dataclass(frozen=True)
class ProviderReviewBinding(ContractValue):
    """Distinct provider layout; the unchanged complete A call binding."""
    call: object

    def __post_init__(self):
        from .task_review import ReviewCallBinding
        _typed(self.call, ReviewCallBinding)
        if self.call.issuer_class != IssuerClass.PRODUCTION_REVIEW.value:
            raise LLMToolError('provider binding invalid; details omitted')

    def to_dict(self):
        values = dict(self.call.__dict__)
        for name in ('ready_task','acceptance_task'): values[name] = values[name].to_dict()
        return {'call':values}

    @property
    def digest(self):
        return _digest('llmtool.provider-receipt-binding.v1', self)

    @property
    def object_key(self):
        # Run/nonce changes cannot buy a second attempt for the same object.
        from .task_review import digest
        c = self.call
        return digest((c.route_digest,c.source_sha,c.ready_task.contract_digest,
                       c.acceptance_task.contract_digest,c.packet_digest),
                      'llmtool.provider-reviewed-object.v1')


@dataclass(frozen=True)
class ProviderObservedState:
    """Host-only observation; its construction is not attestation."""
    binding_digest: str
    source_sha: str
    observation_digest: str
    attested: bool

    def __post_init__(self):
        _hex(self.binding_digest); _hex(self.source_sha,(40,64)); _hex(self.observation_digest)
        _typed(self.attested,bool)


@dataclass(frozen=True)
class ProviderPolicyDecision:
    decision: Decision
    code: DecisionCode
    binding: Optional[ProviderReviewBinding]
    _origin: object = field(default=None,repr=False,compare=False)

    def __post_init__(self):
        if (self._origin is not _DECIDED or type(self.decision) is not Decision or
                type(self.code) is not DecisionCode or
                (self.binding is not None) != (self.decision is Decision.ALLOW_WITH_TRUSTED_RECEIPT)):
            raise LLMToolError('provider decision invalid; details omitted')
        if self.binding is not None: _typed(self.binding,ProviderReviewBinding)
        object.__setattr__(self,'_origin',None)


def decide_provider_review(binding, *, issuer, observed,
                           requirements=frozenset((Requirement.NEW_COST,Requirement.NEW_DATA_EGRESS))):
    """Only this decision can cover vouched cost/data egress; D1 is unchanged."""
    def result(decision,code,b=None):
        return ProviderPolicyDecision(decision,code,b,_DECIDED)
    try:
        _typed(binding,ProviderReviewBinding); _typed(observed,ProviderObservedState)
        _typed(requirements,frozenset)
        if any(type(r) is not Requirement for r in requirements) or requirements & OWNER_P0:
            return result(Decision.DENY,DecisionCode.SEPARATE_AUTHORIZATION_REQUIRED)
        from .review_authority import ProductionReviewIssuer
        if not isinstance(issuer,ProductionReviewIssuer) or issuer.issuer_class is not IssuerClass.PRODUCTION_REVIEW:
            return result(Decision.DENY,DecisionCode.UNTRUSTED_ISSUER)
        c=binding.call
        if (issuer.capability_id != c.issuer_capability_id or
                issuer.vouches_for(c.route_digest) is not True):
            return result(Decision.DENY,DecisionCode.UNTRUSTED_POLICY)
        if (not observed.attested or observed.binding_digest != binding.digest or
                observed.source_sha != c.source_sha or observed.observation_digest != c.observation_digest or
                issuer.matches(c,observed) is not True):
            return result(Decision.DENY,DecisionCode.STATE_MISMATCH)
    except Exception:
        return result(Decision.DENY,DecisionCode.INVALID_REQUEST)
    return result(Decision.ALLOW_WITH_TRUSTED_RECEIPT,DecisionCode.POLICY_ALLOWED,binding)


@dataclass(frozen=True)
class NotificationBinding(ContractValue):
    """Host-only notification scope; never a provider/D1 permission."""
    task: TaskBinding
    acceptance_task: TaskBinding
    source_sha: str
    base_sha: str
    tree_sha: str
    packet_digest: str
    host_pin: str
    route_digest: str
    scope_digest: str
    authors_digest: str
    reviewer_digest: str
    author_session: str
    reviewer_session: str
    epoch: int
    sequence: int
    event_id: str
    source_ref: str
    actor_ref: str
    grant_digest: str
    capability_id: str
    attempt_id: str
    ledger_nonce: str
    not_before: int
    expires_at: int
    rearm_from: str = ''
    tool_mode: str = 'ENFORCED_NO_TOOLS'
    risk_acceptance_digest: str = '0'*64
    coverage_digest: str = '0'*64
    counter_domains: tuple = ('LLMTOOL_PROVIDER_CALLS','REVIEWER_TURN_INFERENCE','CLAUDE_AUTHOR_INFERENCE')

    def __post_init__(self):
        _typed(self.task, TaskBinding); _typed(self.acceptance_task, TaskBinding)
        for name in ('source_sha','base_sha','tree_sha'): _hex(getattr(self,name),(40,64))
        for name in ('packet_digest','host_pin','route_digest','scope_digest','authors_digest',
                     'reviewer_digest','grant_digest','ledger_nonce'): _hex(getattr(self,name))
        for name in ('author_session','reviewer_session','event_id','source_ref','actor_ref','capability_id'):
            _identifier(getattr(self,name))
        _hex(self.attempt_id,(32,))
        if self.rearm_from: _hex(self.rearm_from)
        _hex(self.risk_acceptance_digest); _hex(self.coverage_digest)
        if self.tool_mode not in ("ENFORCED_NO_TOOLS","AUDIT_ONLY_TRIAL") or self.counter_domains != ("LLMTOOL_PROVIDER_CALLS","REVIEWER_TURN_INFERENCE","CLAUDE_AUTHOR_INFERENCE"):
            raise LLMToolError("notification capability invalid; details omitted")
        if self.tool_mode == "AUDIT_ONLY_TRIAL" and (self.risk_acceptance_digest == "0"*64 or self.coverage_digest == "0"*64):
            raise LLMToolError("notification audit acceptance missing; details omitted")
        _integer(self.epoch,minimum=1); _integer(self.sequence,minimum=1,maximum=128)
        _integer(self.not_before); _integer(self.expires_at)
        if (self.author_session == self.reviewer_session or self.task.task_id == self.acceptance_task.task_id or
                not 0 < self.expires_at-self.not_before <= 600):
            raise LLMToolError('notification binding invalid; details omitted')

    @property
    def digest(self): return _digest('llmtool.notification-binding.v1',self)

    @property
    def object_key(self):
        # Exclude run, comment/event, attempt and generation. Fresh grants cannot
        # accidentally repeat an already completed review of this object.
        from .task_review import digest
        return digest({
            'ready':(self.task.task_id,self.task.contract_digest),
            'final':(self.acceptance_task.task_id,self.acceptance_task.contract_digest),
            'source':self.source_sha,'scope':self.scope_digest,'reviewer':self.reviewer_digest},'llmtool.notification-object.v1')


@dataclass(frozen=True)
class NotificationObserved(ContractValue):
    binding_digest: str
    host_pin: str
    attested: bool
    idle: bool
    queue_empty: bool
    exclusive: bool
    no_tools: bool
    withdrawal_supported: bool
    turn_cancel_supported: bool
    audit_coverage: bool = False
    risk_acceptance_digest: str = "0"*64

    def __post_init__(self):
        _hex(self.binding_digest); _hex(self.host_pin)
        _typed(self.audit_coverage,bool); _hex(self.risk_acceptance_digest)
        for name in ('attested','idle','queue_empty','exclusive','no_tools',
                     'withdrawal_supported','turn_cancel_supported'): _typed(getattr(self,name),bool)


@dataclass(frozen=True)
class NotificationDecision:
    decision: Decision
    code: DecisionCode
    binding: Optional[NotificationBinding]
    _origin: object = field(default=None,repr=False,compare=False)

    def __post_init__(self):
        if (self._origin is not _DECIDED or type(self.decision) is not Decision or
                type(self.code) is not DecisionCode or
                (self.binding is not None) != (self.decision is Decision.ALLOW_WITH_TRUSTED_RECEIPT)):
            raise LLMToolError('notification decision invalid; details omitted')
        if self.binding is not None: _typed(self.binding,NotificationBinding)
        object.__setattr__(self,'_origin',None)


def decide_notification(binding, *, issuer, observed):
    """Offline host decision only. No native issuer, parser or public route."""
    def result(decision,code,b=None): return NotificationDecision(decision,code,b,_DECIDED)
    try:
        _typed(binding,NotificationBinding); _typed(observed,NotificationObserved)
        if not isinstance(issuer,HostIssuer) or issuer.issuer_class is not IssuerClass.NOTIFICATION:
            return result(Decision.DENY,DecisionCode.UNTRUSTED_ISSUER)
        if (issuer.capability_id != binding.capability_id or
                issuer.vouches_for(binding.digest) is not True):
            return result(Decision.DENY,DecisionCode.UNTRUSTED_POLICY)
        if (observed.binding_digest != binding.digest or observed.host_pin != binding.host_pin or
                any(getattr(observed,n) is not True for n in ('attested','idle','queue_empty','exclusive',
                    'withdrawal_supported','turn_cancel_supported')) or
                (binding.tool_mode == 'ENFORCED_NO_TOOLS' and observed.no_tools is not True) or
                (binding.tool_mode == 'AUDIT_ONLY_TRIAL' and (observed.audit_coverage is not True or
                 observed.risk_acceptance_digest != binding.risk_acceptance_digest))):
            return result(Decision.DENY,DecisionCode.STATE_MISMATCH)
    except Exception:
        return result(Decision.DENY,DecisionCode.INVALID_REQUEST)
    return result(Decision.ALLOW_WITH_TRUSTED_RECEIPT,DecisionCode.POLICY_ALLOWED,binding)


def notification_id(value):
    """Bound original host IDs before persisting or correlating them."""
    _identifier(value)
