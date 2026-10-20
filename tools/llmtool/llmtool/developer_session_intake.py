"""Bound a next task to an already active Developer Session, without dispatch.

This is an offline admission prerequisite. SessionRegistration and
SessionObservation must come from a trusted Host in a future live composition;
constructing either value in Python is not an attestation. No queue, RPC,
provider, receipt store or CLI is invoked here. In particular PREPARED is not an
ACK or a one-use reservation. The public live entry stays blocked until a
separately reviewed Host transport, durable claim and native grant exist.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from .action_contract import TaskBinding
from .run_budget import RunBudget
from .task_lifecycle import AgentBinding, Role


_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_TERMINAL = frozenset(('COMPLETED', 'FAILED', 'BLOCKED'))


@dataclass(frozen=True)
class SessionRegistration:
    agent: AgentBinding
    source_sha: str
    generation: int
    expires_at: int
    state: str
    prior_task_id: str | None = None
    prior_job_digest: str | None = None


@dataclass(frozen=True)
class SessionObservation:
    agent: AgentBinding
    source_sha: str
    generation: int
    observed_at: int
    active: bool
    receptive: bool


@dataclass(frozen=True)
class PreviousTask:
    task_id: str
    job_digest: str
    status: str
    receipt_digest: str
    integrator_session: str


@dataclass(frozen=True)
class NextTask:
    task: TaskBinding
    agent: AgentBinding
    source_sha: str
    job_digest: str
    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    budget: RunBudget
    expires_at: int
    integrator_session: str


@dataclass(frozen=True)
class IntakeEnvelope:
    status: str
    reason: str
    task_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    job_digest: str | None = None
    source_sha: str | None = None
    integrator_session: str | None = None
    packet_digest: str | None = None
    live_allowed: bool = False
    ack: str = 'NOT_OBSERVED'
    execution_start: str = 'NOT_OBSERVED'
    accepted: bool = False

    def to_dict(self):
        return dict(self.__dict__)


def _hex(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _session(value):
    return (type(value) is str and 0 < len(value) <= 128 and value == value.strip()
            and all(0x20 <= ord(char) < 0x7f for char in value))


def _paths(value, *, required):
    if type(value) is not tuple or (required and not value) or len(value) > 32:
        return False
    if any(type(path) is not str for path in value):
        return False
    if value != tuple(sorted(set(value))):
        return False
    for path in value:
        if (type(path) is not str or len(path) > 512 or not path or
                path.startswith('/') or '\\' in path or
                any(part in ('', '.', '..') for part in path.split('/')) or
                any(ord(char) < 0x20 or ord(char) >= 0x7f for char in path)):
            return False
    return True


def _blocked(reason):
    return IntakeEnvelope('BLOCKED', reason)


def prepare_next(*, next_task, registration, observation, previous, now):
    """Check exact task/session continuity; produce data only, never authority.

    All inputs are caller supplied. A future trusted Host must obtain and verify
    registration/observation and reserve the task durably before dispatch.
    This function deliberately does not infer ACK, execution or receipt from
    active PID, queue capability, event text, or a terminal prior task.
    """
    if (type(next_task) is not NextTask or
            type(registration) is not SessionRegistration or
            type(observation) is not SessionObservation):
        return _blocked('INTAKE_EVIDENCE_MISSING')
    n, r, o = next_task, registration, observation
    if (type(n.task) is not TaskBinding or type(n.agent) is not AgentBinding or
            n.agent.role is not Role.DEVELOPER or type(n.budget) is not RunBudget or
            not _hex(n.source_sha, _HEX40) or not _hex(n.job_digest, _HEX64) or
            not _session(n.integrator_session)):
        return _blocked('INTAKE_TASK_INVALID')
    if (type(r.agent) is not AgentBinding or type(o.agent) is not AgentBinding or
            r.agent != n.agent or o.agent != n.agent or
            r.source_sha != n.source_sha or o.source_sha != n.source_sha or
            type(r.generation) is not int or r.generation < 1 or
            type(o.generation) is not int or o.generation != r.generation):
        return _blocked('INTAKE_SESSION_MISMATCH')
    if (r.state != 'ACTIVE' or o.active is not True or o.receptive is not True):
        return _blocked('INTAKE_SESSION_UNAVAILABLE')
    if (type(now) is not int or type(o.observed_at) is not int or
            type(r.expires_at) is not int or type(n.expires_at) is not int or
            o.observed_at > now or now - o.observed_at > 30 or
            now >= min(r.expires_at, n.expires_at) or
            o.observed_at < 0 or now < 0):
        return _blocked('INTAKE_EXPIRED_OR_CLOCK_INVALID')
    if (not _paths(n.read_paths, required=True) or
            not _paths(n.write_paths, required=False) or
            not set(n.write_paths).issubset(n.read_paths)):
        return _blocked('INTAKE_SCOPE_INVALID')
    if (r.prior_task_id is None or r.prior_job_digest is None or
            previous is None):
        return _blocked('INTAKE_PREVIOUS_NOT_TERMINAL')
    if (type(previous) is not PreviousTask or
            type(previous.task_id) is not str or not previous.task_id or
            previous.task_id != r.prior_task_id or
            previous.task_id == n.task.task_id or
            not _hex(previous.job_digest, _HEX64) or
            previous.job_digest != r.prior_job_digest or
            previous.job_digest == n.job_digest or
            type(previous.status) is not str or previous.status not in _TERMINAL or
            not _hex(previous.receipt_digest, _HEX64) or
            previous.integrator_session != n.integrator_session):
        return _blocked('INTAKE_PREVIOUS_NOT_TERMINAL')
    body = dict(task=n.task.to_dict(), agent=n.agent.to_dict(),
                source_sha=n.source_sha, job_digest=n.job_digest,
                read_paths=n.read_paths, write_paths=n.write_paths,
                budget=n.budget.to_dict(), expires_at=n.expires_at,
                integrator_session=n.integrator_session,
                registration=dict(generation=r.generation, expires_at=r.expires_at,
                                  state=r.state, prior_task_id=r.prior_task_id,
                                  prior_job_digest=r.prior_job_digest),
                observation=dict(observed_at=o.observed_at, active=o.active,
                                 receptive=o.receptive),
                previous=previous.__dict__)
    packet = hashlib.sha256(b'llmtool.developer-session-intake.v1\0' +
                            json.dumps(body, sort_keys=True, separators=(',', ':'),
                                       ensure_ascii=True).encode('ascii')).hexdigest()
    return IntakeEnvelope('PREPARED', 'INTAKE_BOUNDS_MATCH', n.task.task_id,
                          n.agent.agent_id, n.agent.session_id, n.job_digest,
                          n.source_sha, n.integrator_session, packet)


def run_live(_prepared):
    """Explicitly refuse effects until native Host, reservation and review exist."""
    return _blocked('LIVE_SESSION_TRANSPORT_NOT_ADMITTED')
