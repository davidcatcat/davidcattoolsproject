"""S1a caller-supplied task replay. No clock, collector or execution authority."""

import json
import re
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .action_contract import (ContractValue, TaskBinding, _collection, _hex,
                              _identifier, _integer, _load, _object, _path,
                              _text, _typed, _version, _pairs)
from .diagnostics import safe_diagnostic_text
from .errors import coded_error
from .task_verification import (TaskContract, EvidenceRecord, EvidenceSource,
                                VerificationStatus, parse_evidence_record,
                                verify_task)

FILE_LIMIT = OUTPUT_LIMIT = 1024 * 1024
EVENT_LIMIT = 16 * 1024
MAX_EVENTS = 128
MAX_AGENTS = MAX_REFS = 32


def invalid(code='TASK_INPUT_INVALID'):
    exc = coded_error('task input invalid; details omitted', 'USER_INPUT_ERROR', 'input')
    exc.lifecycle_code = code
    raise exc


def _id(value):
    _identifier(value)
    safe_diagnostic_text(value, 64)


def _privacy(value):
    if type(value) is str:
        safe_diagnostic_text(value, 1024)
    elif type(value) in (tuple, list):
        for item in value:
            _privacy(item)
    elif type(value) is dict:
        for item in value.values():
            _privacy(item)


def _strings(values, validator, maximum=MAX_REFS):
    values = _collection(values, str, maximum, 0)
    for value in values:
        validator(value)
    if values != tuple(sorted(set(values))):
        invalid()
    return values


def utc_time(value):
    """Explicit unambiguous second-resolution UTC only; never consult time.now."""
    if type(value) is not str or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value) is None:
        invalid('TASK_CLOCK_INVALID')
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        invalid('TASK_CLOCK_INVALID')


class Role(str, Enum):
    DEVELOPER = 'DEVELOPER'
    REVIEWER = 'REVIEWER'


class State(str, Enum):
    QUEUED = 'QUEUED'
    STARTING = 'STARTING'
    RUNNING = 'RUNNING'
    WAITING_REVIEW = 'WAITING_REVIEW'
    BLOCKED = 'BLOCKED'
    FAILED = 'FAILED'
    COMPLETED = 'COMPLETED'


@dataclass(frozen=True)
class AgentBinding(ContractValue):
    agent_id: str
    session_id: str
    role: Role
    root: str
    common_dir: str
    branch: str

    def __post_init__(self):
        _id(self.agent_id)
        _id(self.session_id)
        _typed(self.role, Role)
        _path(self.root, absolute=True)
        if type(self.common_dir) is not str or not self.common_dir.endswith('/.git'):
            invalid()
        _path(self.common_dir[:-5], absolute=True)
        _path(self.branch)
        _text(self.branch, 256)
        if self.role is Role.DEVELOPER and self.branch in ('main', 'automation/ai-loop'):
            invalid('TASK_BINDING_MISMATCH')
        _privacy(self.to_dict())


@dataclass(frozen=True)
class Expectations(ContractValue):
    schema_version: int
    task: TaskBinding
    source_sha: str
    epoch: int
    initial_state: State
    initial_sequence: int
    agents: tuple[AgentBinding, ...]
    authored_by: Optional[tuple[AgentBinding, ...]]
    heartbeat_ttl_seconds: int
    check_digest: str
    artifact_digests: tuple[str, ...]

    def __post_init__(self):
        _version(self.schema_version)
        _typed(self.task, TaskBinding)
        _hex(self.source_sha, (40, 64))
        _integer(self.epoch, minimum=1)
        _typed(self.initial_state, State)
        _integer(self.initial_sequence, maximum=0)
        if self.initial_state is not State.QUEUED:
            invalid('TASK_STATE_INVALID')
        agents = _collection(self.agents, AgentBinding, MAX_AGENTS)
        if (len({(a.agent_id, a.session_id) for a in agents}) != len(agents) or
                {a.role for a in agents} != {Role.DEVELOPER, Role.REVIEWER}):
            invalid('TASK_BINDING_MISMATCH')
        authors = (None if self.authored_by is None else
                   _collection(self.authored_by, AgentBinding, MAX_AGENTS, 0))
        if authors is not None and (any(a.role is not Role.DEVELOPER for a in authors) or
                len(set(authors)) != len(authors)):
            invalid('TASK_BINDING_MISMATCH')
        developers = tuple(a for a in agents if a.role is Role.DEVELOPER)
        for reviewer in agents:
            if reviewer.role is Role.REVIEWER and any(
                    reviewer.agent_id == author.agent_id or
                    reviewer.session_id == author.session_id
                    for author in (*developers, *(authors or ()))):
                invalid('TASK_REVIEW_NOT_INDEPENDENT')
        _integer(self.heartbeat_ttl_seconds, minimum=1, maximum=86400)
        _hex(self.check_digest)
        object.__setattr__(self, 'agents', agents)
        object.__setattr__(self, 'authored_by', authors)
        object.__setattr__(self, 'artifact_digests', _strings(self.artifact_digests, _hex))
        _privacy(self.to_dict())


@dataclass(frozen=True)
class HandoffRecord(ContractValue):
    task: TaskBinding
    source_sha: str
    epoch: int
    from_agent: AgentBinding
    to_agent: AgentBinding
    check_digest: str
    artifact_digests: tuple[str, ...]
    prior_event_id: str
    outstanding_criteria: tuple[str, ...]

    def __post_init__(self):
        _typed(self.task, TaskBinding)
        _hex(self.source_sha, (40, 64))
        _integer(self.epoch, minimum=1)
        _typed(self.from_agent, AgentBinding)
        _typed(self.to_agent, AgentBinding)
        _hex(self.check_digest)
        _id(self.prior_event_id)
        object.__setattr__(self, 'artifact_digests', _strings(self.artifact_digests, _hex))
        object.__setattr__(self, 'outstanding_criteria', _strings(self.outstanding_criteria, _id))


@dataclass(frozen=True)
class TaskEvent(ContractValue):
    schema_version: int
    event_id: str
    sequence: int
    epoch: int
    task: TaskBinding
    source_sha: str
    agent: AgentBinding
    kind: str
    state: State
    prior_event_id: Optional[str]
    prior_state: Optional[State]
    timestamp: str
    provenance_digest: Optional[str]
    evidence: tuple[EvidenceRecord, ...]
    handoff: Optional[HandoffRecord]

    def __post_init__(self):
        _version(self.schema_version)
        _id(self.event_id)
        _integer(self.sequence, minimum=1, maximum=MAX_EVENTS)
        _integer(self.epoch, minimum=1)
        _typed(self.task, TaskBinding)
        _hex(self.source_sha, (40, 64))
        _typed(self.agent, AgentBinding)
        if type(self.kind) is not str or self.kind not in ('STATE', 'RESUME'):
            invalid('TASK_STATE_INVALID')
        _typed(self.state, State)
        if self.prior_event_id is not None:
            _id(self.prior_event_id)
        if self.prior_state is not None:
            _typed(self.prior_state, State)
        utc_time(self.timestamp)
        if self.provenance_digest is not None:
            _hex(self.provenance_digest)
        object.__setattr__(self, 'evidence', _collection(self.evidence, EvidenceRecord, MAX_REFS, 0))
        if self.handoff is not None:
            _typed(self.handoff, HandoffRecord)
        value = self.to_dict()
        _privacy(value)
        if len(json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')) > EVENT_LIMIT:
            invalid()


def _value(model, raw):
    value = dict(_object(raw, (f.name for f in fields(model))))
    if model is AgentBinding:
        value['role'] = Role(value['role'])
    elif model is HandoffRecord:
        value['task'] = _value(TaskBinding, value['task'])
        value['from_agent'] = _value(AgentBinding, value['from_agent'])
        value['to_agent'] = _value(AgentBinding, value['to_agent'])
    elif model in (Expectations, TaskEvent):
        value['task'] = _value(TaskBinding, value['task'])
        if model is Expectations:
            value['initial_state'] = State(value['initial_state'])
            if (type(value['agents']) is not list or len(value['agents']) > MAX_AGENTS or
                    (value['authored_by'] is not None and (type(value['authored_by']) is not list or
                                                         len(value['authored_by']) > MAX_AGENTS))):
                invalid()
            value['agents'] = tuple(_value(AgentBinding, a) for a in value['agents'])
            if value['authored_by'] is not None:
                value['authored_by'] = tuple(_value(AgentBinding, a) for a in value['authored_by'])
        else:
            value['agent'] = _value(AgentBinding, value['agent'])
            value['state'] = State(value['state'])
            if value['prior_state'] is not None:
                value['prior_state'] = State(value['prior_state'])
            if type(value['evidence']) is not list or len(value['evidence']) > MAX_REFS:
                invalid()
            value['evidence'] = tuple(parse_evidence_record(json.dumps(e, allow_nan=False))
                                      for e in value['evidence'])
            if value['handoff'] is not None:
                value['handoff'] = _value(HandoffRecord, value['handoff'])
    return model(**value)


def parse_expectations(text):
    # Auxiliary envelope can exceed the unchanged TaskContract 64KiB bound.
    if type(text) is not str or len(text.encode('utf-8')) > FILE_LIMIT:
        invalid()
    depth = 0
    quoted = escaped = False
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
            if depth > 8:
                invalid()
        elif char in ']}':
            depth -= 1
    def bounded_integer(value):
        if len(value.lstrip('-')) > 19:
            invalid()
        return int(value)
    raw = json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda _: invalid(),
                     parse_float=lambda _: invalid(), parse_int=bounded_integer)
    return _value(Expectations, raw)


def parse_events(text):
    if type(text) is not str or len(text.encode('utf-8')) > FILE_LIMIT:
        invalid()
    lines = text.splitlines()
    if len(lines) > MAX_EVENTS:
        invalid()
    events = []
    for line in lines:
        if len(line.encode('utf-8')) > EVENT_LIMIT:
            invalid()
        events.append(_value(TaskEvent, _load(line)))
    return tuple(events)


_TRANSITIONS = {
    State.QUEUED: (State.STARTING,),
    State.STARTING: (State.RUNNING, State.BLOCKED, State.FAILED),
    State.RUNNING: (State.WAITING_REVIEW, State.BLOCKED, State.FAILED),
    State.WAITING_REVIEW: (State.COMPLETED, State.BLOCKED, State.FAILED),
}


@dataclass(frozen=True)
class InspectionResult(ContractValue):
    task: TaskBinding
    source_sha: str
    epoch: int
    reported_state: Optional[State]
    last_sequence: int
    structural_validation: str
    findings: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    evidence_structure: str
    handoff_preview: Optional[HandoffRecord]

    @property
    def exit_code(self):
        return 0 if self.structural_validation == 'SUCCESS' else 1

    def to_dict(self):
        value = super().to_dict()
        # Do not echo filesystem paths or logical Session IDs into previews.
        if self.handoff_preview is not None:
            h = self.handoff_preview
            value['handoff_preview'] = {
                'from_agent_id': h.from_agent.agent_id, 'to_agent_id': h.to_agent.agent_id,
                'prior_event_id': h.prior_event_id, 'source_sha': h.source_sha,
                'contract_digest': h.task.contract_digest, 'epoch': h.epoch,
                'check_digest': h.check_digest, 'artifact_digests': list(h.artifact_digests),
                'outstanding_criteria': list(h.outstanding_criteria), 'triggered': False,
            }
        return {**value, 'schema_version': 1, 'runtime_evidence_status': 'NOT_VERIFIED',
                'runtime_missing_evidence': ['EXECUTION_PROVENANCE', 'INDEPENDENT_REVIEW_ATTESTATION'],
                'authorized': False, 'bindings_source': 'CALLER_SUPPLIED',
                'code': 'TASK_' + self.structural_validation}


def inspect_task(contract, expect, events, observed_at):
    _typed(contract, TaskContract)
    _typed(expect, Expectations)
    events = _collection(events, TaskEvent, MAX_EVENTS, 0)
    observed = utc_time(observed_at)
    if expect.task != contract.binding(expect.task.run_id):
        invalid('TASK_BINDING_MISMATCH')
    criteria = {c.criterion_id for c in contract.criteria}
    evidence = []
    seen = set()
    previous = None
    preview = None
    findings = set()
    authors = expect.authored_by
    if not authors or any(a not in authors for a in expect.agents if a.role is Role.DEVELOPER):
        findings.add('AUTHORSHIP_INCOMPLETE')
    for event in events:
        if (event.task != expect.task or event.source_sha != expect.source_sha or
                event.epoch != expect.epoch or event.agent not in expect.agents):
            invalid('TASK_BINDING_MISMATCH')
        if event.event_id in seen or event.sequence != len(seen) + 1:
            invalid('TASK_SEQUENCE_INVALID')
        seen.add(event.event_id)
        stamp = utc_time(event.timestamp)
        if stamp > observed or (previous is not None and stamp < utc_time(previous.timestamp)):
            invalid('TASK_CLOCK_INVALID')
        if previous is None:
            if (event.state is not State.QUEUED or event.kind != 'STATE' or
                    event.prior_event_id is not None or event.prior_state is not None):
                invalid('TASK_STATE_INVALID')
        else:
            if event.prior_event_id != previous.event_id or event.prior_state is not previous.state:
                invalid('TASK_STATE_INVALID')
            if previous.state is State.BLOCKED:
                if (event.kind != 'RESUME' or event.state is not State.STARTING or
                        stamp <= utc_time(previous.timestamp)):
                    invalid('TASK_STATE_INVALID')
                preview = None
            elif event.kind != 'STATE' or event.state not in _TRANSITIONS.get(previous.state, ()):
                invalid('TASK_STATE_INVALID')
        if event.state in (State.QUEUED, State.STARTING, State.RUNNING, State.WAITING_REVIEW):
            if event.agent.role is not Role.DEVELOPER:
                invalid('TASK_BINDING_MISMATCH')
        elif event.agent.role is Role.REVIEWER and (preview is None or event.agent != preview.to_agent):
            invalid('TASK_BINDING_MISMATCH')
        if event.state is State.COMPLETED and event.agent.role is not Role.REVIEWER:
            invalid('TASK_REVIEW_NOT_INDEPENDENT')
        if event.provenance_digest is None:
            findings.add('PROVENANCE_INCOMPLETE')
        for record in event.evidence:
            if (record.task != expect.task or record.subject_digest != expect.source_sha or
                    record.criterion_id not in criteria or
                    (record.source is EvidenceSource.REVIEW and
                     (event.agent.role is not Role.REVIEWER or preview is None or
                      event.agent != preview.to_agent)) or
                    (record.source is EvidenceSource.COMMAND and record.command_digest != expect.check_digest) or
                    not set(record.artifact_digests).issubset(expect.artifact_digests)):
                invalid('TASK_BINDING_MISMATCH')
            evidence.append(record)
        if len(evidence) > 128 or len({e.evidence_id for e in evidence}) != len(evidence):
            invalid('TASK_SEQUENCE_INVALID')
        if event.handoff is not None:
            h = event.handoff
            if (event.state is not State.WAITING_REVIEW or previous is None or
                    h.task != expect.task or h.source_sha != expect.source_sha or h.epoch != expect.epoch or
                    h.from_agent != event.agent or h.to_agent not in expect.agents or
                    h.from_agent.role is not Role.DEVELOPER or h.to_agent.role is not Role.REVIEWER or
                    h.check_digest != expect.check_digest or h.artifact_digests != expect.artifact_digests or
                    h.prior_event_id != previous.event_id):
                invalid('TASK_HANDOFF_INVALID')
            current = verify_task(contract, pinned_digest=expect.task.contract_digest,
                                  run_id=expect.task.run_id, subject_digest=expect.source_sha,
                                  evidence=tuple(evidence))
            outstanding = tuple(c.criterion_id for c in current.criteria if c.status is not VerificationStatus.PASS)
            if h.outstanding_criteria != outstanding:
                invalid('TASK_HANDOFF_INVALID')
            preview = h
        elif event.state is State.WAITING_REVIEW:
            findings.add('HANDOFF_INCOMPLETE')
        previous = event
    verified = verify_task(contract, pinned_digest=expect.task.contract_digest,
                           run_id=expect.task.run_id, subject_digest=expect.source_sha,
                           evidence=tuple(evidence))
    missing = tuple(c.criterion_id for c in verified.criteria if c.status is not VerificationStatus.PASS)
    if missing:
        findings.add('REQUIRED_EVIDENCE_INCOMPLETE')
    # Rejected supplemental evidence caps verify_task even when all criterion
    # rows have PASS. Never promote that aggregate INCOMPLETE to success.
    status = 'INCOMPLETE' if verified.status is not VerificationStatus.PASS or findings else 'SUCCESS'
    findings.update(f.code.value for f in verified.findings)
    if not events:
        findings.add('EVENTS_INCOMPLETE')
        status = 'INCOMPLETE'
    elif (observed - utc_time(events[-1].timestamp)).total_seconds() >= expect.heartbeat_ttl_seconds:
        findings.add('HEARTBEAT_EXPIRED')
        status = 'STALE'
    return InspectionResult(expect.task, expect.source_sha, expect.epoch,
                            previous.state if previous else None, len(events), status,
                            tuple(sorted(findings)), missing, verified.status.value, preview)
