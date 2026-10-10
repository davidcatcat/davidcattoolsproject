"""A11b.5 offline task verification over a pinned Task Contract; no authority.

Structural verification of caller-supplied evidence only. A record saying a
command PASSED is not proof that the command ran: no command, file, Git state,
provider, clock or environment is consulted here. Model-reported success is
never task success: MODEL_CLAIM evidence satisfies nothing. A VERIFIED result
(status PASS) is not DONE; required review/merge gates remain separate, and
results are always authorized=False. A11a run status/schema and CLI exit codes
are unchanged; there is no CLI route.
"""

from dataclasses import dataclass, fields
from enum import Enum
from typing import Optional

from .action_contract import (ContractValue, TaskBinding, _collection, _digest, _enum,
                              _hex, _identifier, _integer, _load, _object, _path,
                              _scope, _text, _typed, _version)
from .diagnostics import safe_diagnostic_text
from .errors import LLMToolError

MAX_CRITERIA = 32
MAX_SCOPE = 32
MAX_EVIDENCE = 128
MAX_ARTIFACTS = 32
MAX_FINDINGS = MAX_EVIDENCE + MAX_CRITERIA + 1
GOAL_BYTES = 1024
DESCRIPTION_BYTES = 512


def _invalid():
    raise LLMToolError('task verification invalid; details omitted')


def _safe_text(value, limit):
    # Bounded control-free text, then credential screening; never echoes value.
    _text(value, limit)
    try:
        safe_diagnostic_text(value, limit)
    except ValueError:
        rejected = True
    else:
        rejected = False
    if rejected:
        _invalid()
    return value


def _unique_sorted(value):
    if tuple(sorted(set(value))) != tuple(value):
        _invalid()
    return value


class CriterionKind(str, Enum):
    DETERMINISTIC_CHECK = 'DETERMINISTIC_CHECK'
    REVIEW = 'REVIEW'
    ARTIFACT = 'ARTIFACT'


class EvidenceSource(str, Enum):
    COMMAND = 'COMMAND'
    REVIEW = 'REVIEW'
    ARTIFACT = 'ARTIFACT'
    MODEL_CLAIM = 'MODEL_CLAIM'  # Recorded for audit; never satisfies a criterion.


class EvidenceOutcome(str, Enum):
    PASSED = 'PASSED'
    FAILED = 'FAILED'
    INTERRUPTED = 'INTERRUPTED'
    NOT_RUN = 'NOT_RUN'


class VerificationStatus(str, Enum):
    PASS = 'PASS'
    FAIL = 'FAIL'
    INCOMPLETE = 'INCOMPLETE'
    NOT_TESTED = 'NOT_TESTED'


_SOURCE_FOR_KIND = {CriterionKind.DETERMINISTIC_CHECK: EvidenceSource.COMMAND,
                    CriterionKind.REVIEW: EvidenceSource.REVIEW,
                    CriterionKind.ARTIFACT: EvidenceSource.ARTIFACT}
_RANK = {VerificationStatus.PASS: 0, VerificationStatus.NOT_TESTED: 1,
         VerificationStatus.INCOMPLETE: 2, VerificationStatus.FAIL: 3}


@dataclass(frozen=True)
class AcceptanceCriterion(ContractValue):
    criterion_id: str
    kind: CriterionKind
    description: str

    def __post_init__(self):
        _identifier(self.criterion_id)
        _typed(self.kind, CriterionKind)
        _safe_text(self.description, DESCRIPTION_BYTES)


@dataclass(frozen=True)
class TaskContract(ContractValue):
    """Immutable, versioned contract; a new version never inherits VERIFIED."""
    schema_version: int
    task_id: str
    contract_version: int
    previous_digest: Optional[str]
    goal: str
    authorized_scope: tuple[str, ...]
    forbidden_scope: tuple[str, ...]
    criteria: tuple[AcceptanceCriterion, ...]

    def __post_init__(self):
        _version(self.schema_version)
        _identifier(self.task_id)
        _integer(self.contract_version, maximum=2**31-1, minimum=1)
        if self.contract_version == 1:
            if self.previous_digest is not None:
                _invalid()
        else:
            _hex(self.previous_digest)
        _safe_text(self.goal, GOAL_BYTES)
        authorized = _scope(self.authorized_scope)
        forbidden = _collection(self.forbidden_scope, str, MAX_SCOPE, 0)
        for path in forbidden:
            _path(path)
        _unique_sorted(forbidden)
        # Forbidden may carve out a descendant of an authorized path, but may not
        # equal or contain one (that would make the authorized path meaningless).
        if any(a == f or a.startswith(f + '/') for a in authorized for f in forbidden):
            _invalid()
        criteria = _collection(self.criteria, AcceptanceCriterion, MAX_CRITERIA)
        _unique_sorted(tuple(c.criterion_id for c in criteria))
        object.__setattr__(self, 'authorized_scope', authorized)
        object.__setattr__(self, 'forbidden_scope', forbidden)
        object.__setattr__(self, 'criteria', criteria)
        self.to_json()

    @property
    def digest(self):
        return _digest('llmtool.task-contract', self)

    def binding(self, run_id):
        return TaskBinding(self.task_id, run_id, self.contract_version, self.digest)


def is_contract_successor(previous, current):
    """Structural chain check only; NOT proof the user authorized the change."""
    _typed(previous, TaskContract)
    _typed(current, TaskContract)
    return (current.task_id == previous.task_id and
            current.contract_version == previous.contract_version + 1 and
            current.previous_digest == previous.digest)


@dataclass(frozen=True)
class EvidenceRecord(ContractValue):
    """Caller-asserted evidence metadata (digests/references, never raw output)."""
    schema_version: int
    evidence_id: str
    criterion_id: str
    task: TaskBinding
    subject_digest: str
    source: EvidenceSource
    outcome: EvidenceOutcome
    command_digest: Optional[str]
    exit_status: Optional[int]
    environment_digest: Optional[str]
    artifact_digests: tuple[str, ...]
    complete: bool

    def __post_init__(self):
        _version(self.schema_version)
        _identifier(self.evidence_id)
        _identifier(self.criterion_id)
        _typed(self.task, TaskBinding)
        _hex(self.subject_digest, (40, 64))
        _typed(self.source, EvidenceSource)
        _typed(self.outcome, EvidenceOutcome)
        if self.source is EvidenceSource.COMMAND:
            _hex(self.command_digest)
            _hex(self.environment_digest)
            if self.outcome in (EvidenceOutcome.PASSED, EvidenceOutcome.FAILED):
                _integer(self.exit_status, maximum=255, minimum=-255)
            elif self.outcome is EvidenceOutcome.INTERRUPTED:
                if self.exit_status is not None:
                    _integer(self.exit_status, maximum=255, minimum=-255)
            elif self.exit_status is not None:
                _invalid()
            if self.outcome is EvidenceOutcome.PASSED and self.exit_status != 0:
                _invalid()
        elif (self.command_digest is not None or self.exit_status is not None or
              self.environment_digest is not None):
            _invalid()
        artifacts = _collection(self.artifact_digests, str, MAX_ARTIFACTS, 0)
        for value in artifacts:
            _hex(value)
        _unique_sorted(artifacts)
        if (self.source is EvidenceSource.ARTIFACT and
                self.outcome is EvidenceOutcome.PASSED and not artifacts):
            _invalid()
        _typed(self.complete, bool)
        object.__setattr__(self, 'artifact_digests', artifacts)
        self.to_json()


class VerificationCode(str, Enum):
    CONTRACT_NOT_PINNED = 'CONTRACT_NOT_PINNED'
    BINDING_MISMATCH = 'BINDING_MISMATCH'
    SUBJECT_MISMATCH = 'SUBJECT_MISMATCH'
    UNKNOWN_CRITERION = 'UNKNOWN_CRITERION'
    SOURCE_KIND_MISMATCH = 'SOURCE_KIND_MISMATCH'
    MODEL_CLAIM_IGNORED = 'MODEL_CLAIM_IGNORED'
    MISSING_EVIDENCE = 'MISSING_EVIDENCE'
    EVIDENCE_FAILED = 'EVIDENCE_FAILED'
    EVIDENCE_INTERRUPTED = 'EVIDENCE_INTERRUPTED'
    EVIDENCE_PARTIAL = 'EVIDENCE_PARTIAL'
    EVIDENCE_NOT_RUN = 'EVIDENCE_NOT_RUN'


@dataclass(frozen=True)
class VerificationFinding(ContractValue):
    code: VerificationCode
    criterion_id: Optional[str]
    evidence_id: Optional[str]

    def __post_init__(self):
        _typed(self.code, VerificationCode)
        for value in (self.criterion_id, self.evidence_id):
            if value is not None:
                _identifier(value)

    def sort_key(self):
        return (self.code.value, self.criterion_id or '', self.evidence_id or '')


@dataclass(frozen=True)
class CriterionResult(ContractValue):
    criterion_id: str
    status: VerificationStatus
    evidence_ids: tuple[str, ...]

    def __post_init__(self):
        _identifier(self.criterion_id)
        _typed(self.status, VerificationStatus)
        ids = _collection(self.evidence_ids, str, MAX_EVIDENCE, 0)
        for value in ids:
            _identifier(value)
        object.__setattr__(self, 'evidence_ids', _unique_sorted(ids))


@dataclass(frozen=True)
class VerificationResult(ContractValue):
    """PASS means VERIFIED against the pinned contract; it is never DONE/authority."""
    schema_version: int
    contract_digest: str
    subject_digest: str
    status: VerificationStatus
    criteria: tuple[CriterionResult, ...]
    findings: tuple[VerificationFinding, ...]

    def __post_init__(self):
        _version(self.schema_version)
        _hex(self.contract_digest)
        _hex(self.subject_digest, (40, 64))
        _typed(self.status, VerificationStatus)
        object.__setattr__(self, 'criteria',
                           _collection(self.criteria, CriterionResult, MAX_CRITERIA))
        findings = _collection(self.findings, VerificationFinding, MAX_FINDINGS, 0)
        if tuple(sorted(findings, key=VerificationFinding.sort_key)) != findings:
            _invalid()
        object.__setattr__(self, 'findings', findings)

    @property
    def verified(self):
        return self.status is VerificationStatus.PASS

    @property
    def authorized(self):
        return False

    def to_dict(self):
        return {**super().to_dict(), 'authorized': False}


def _rejection(record, kinds, binding, subject_digest):
    if record.source is EvidenceSource.MODEL_CLAIM:
        return VerificationCode.MODEL_CLAIM_IGNORED
    if record.task != binding:
        return VerificationCode.BINDING_MISMATCH
    if record.subject_digest != subject_digest:
        return VerificationCode.SUBJECT_MISMATCH
    if record.criterion_id not in kinds:
        return VerificationCode.UNKNOWN_CRITERION
    if record.source is not _SOURCE_FOR_KIND[kinds[record.criterion_id]]:
        return VerificationCode.SOURCE_KIND_MISMATCH
    return None


def _record_status(record):
    if record.outcome is EvidenceOutcome.PASSED:
        if record.complete:
            return VerificationStatus.PASS, None
        return VerificationStatus.INCOMPLETE, VerificationCode.EVIDENCE_PARTIAL
    return {EvidenceOutcome.FAILED: (VerificationStatus.FAIL, VerificationCode.EVIDENCE_FAILED),
            EvidenceOutcome.INTERRUPTED: (VerificationStatus.INCOMPLETE,
                                          VerificationCode.EVIDENCE_INTERRUPTED),
            EvidenceOutcome.NOT_RUN: (VerificationStatus.NOT_TESTED,
                                      VerificationCode.EVIDENCE_NOT_RUN)}[record.outcome]


def verify_task(contract, *, pinned_digest, run_id, subject_digest, evidence):
    """Deterministic, order-independent verdict; invalid inputs fail closed."""
    _typed(contract, TaskContract)
    _hex(pinned_digest)
    _hex(run_id, (32,))
    _hex(subject_digest, (40, 64))
    evidence = _collection(evidence, EvidenceRecord, MAX_EVIDENCE, 0)
    if len({record.evidence_id for record in evidence}) != len(evidence):
        _invalid()
    findings = []
    statuses = {c.criterion_id: [] for c in contract.criteria}
    accepted = {c.criterion_id: [] for c in contract.criteria}
    unpinned = contract.digest != pinned_digest
    capped = unpinned
    if unpinned:
        findings.append(VerificationFinding(VerificationCode.CONTRACT_NOT_PINNED, None, None))
    else:
        kinds = {c.criterion_id: c.kind for c in contract.criteria}
        binding = contract.binding(run_id)
        for record in evidence:
            code = _rejection(record, kinds, binding, subject_digest)
            if code is None:
                status, code = _record_status(record)
                statuses[record.criterion_id].append(status)
                accepted[record.criterion_id].append(record.evidence_id)
            elif code is not VerificationCode.MODEL_CLAIM_IGNORED:
                capped = True
            if code is not None:
                findings.append(VerificationFinding(code, record.criterion_id,
                                                    record.evidence_id))
    criteria = []
    for criterion in contract.criteria:
        found = statuses[criterion.criterion_id]
        if not found and not unpinned:
            findings.append(VerificationFinding(VerificationCode.MISSING_EVIDENCE,
                                                criterion.criterion_id, None))
        status = max(found, key=_RANK.__getitem__, default=VerificationStatus.NOT_TESTED)
        criteria.append(CriterionResult(criterion.criterion_id, status,
                                        tuple(sorted(accepted[criterion.criterion_id]))))
    states = {c.status for c in criteria}
    if VerificationStatus.FAIL in states:
        overall = VerificationStatus.FAIL
    elif not capped and states == {VerificationStatus.PASS}:
        overall = VerificationStatus.PASS
    elif not capped and states == {VerificationStatus.NOT_TESTED}:
        overall = VerificationStatus.NOT_TESTED
    else:
        overall = VerificationStatus.INCOMPLETE
    return VerificationResult(1, contract.digest, subject_digest, overall, tuple(criteria),
                              tuple(sorted(findings, key=VerificationFinding.sort_key)))


def _list(value):
    if type(value) is not list:
        _invalid()
    return value


def parse_task_contract(text):
    value = _object(_load(text), (f.name for f in fields(TaskContract)))
    criteria = tuple(AcceptanceCriterion(item['criterion_id'],
                                         _enum(CriterionKind, item['kind']), item['description'])
                     for item in (_object(raw, ('criterion_id', 'kind', 'description'))
                                  for raw in _list(value['criteria'])))
    return TaskContract(value['schema_version'], value['task_id'], value['contract_version'],
                        value['previous_digest'], value['goal'],
                        _list(value['authorized_scope']), _list(value['forbidden_scope']),
                        criteria)


def parse_evidence_record(text):
    value = _object(_load(text), (f.name for f in fields(EvidenceRecord)))
    task = TaskBinding(**_object(value['task'], (f.name for f in fields(TaskBinding))))
    return EvidenceRecord(value['schema_version'], value['evidence_id'], value['criterion_id'],
                          task, value['subject_digest'], _enum(EvidenceSource, value['source']),
                          _enum(EvidenceOutcome, value['outcome']), value['command_digest'],
                          value['exit_status'], value['environment_digest'],
                          _list(value['artifact_digests']), value['complete'])
