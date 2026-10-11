"""A11b.5 run budgets: pure immutable data and predicates, not an executor.

All state is caller-supplied; no filesystem, provider, clock or subprocess is used.
Nothing here grants, reserves or enforces anything at runtime yet (no executor).
assess_budget is retrospective: used == limit is still WITHIN. admit_next is
prospective: it denies one more call once any limit is reached, not only exceeded,
so its *_EXCEEDED codes mean "no capacity left". Missing usage or elapsed time is
UNKNOWN, never zero; a known partial subtotal is reported as partial
(unknown_calls > 0), never as a total. Unknown never counts as within.

Coordinator decision (PR #104): admit_next keeps denying admission (USAGE_UNKNOWN)
once any recorded call lacks usage, so a provider that never reports usage cannot
make a second call under a token budget; there is no "no token budget" bypass.
"""

from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Optional

from .action_contract import (ContractValue, _collection, _hex, _identifier,
                              _integer, _typed, _version)
from .diagnostics import MAX_TOKEN_COUNT, validate_provider_usage
from .errors import LLMToolError

MAX_REPAIR_ATTEMPTS = 2
MAX_PROVIDER_CALLS = 64
MAX_WALL_CLOCK_MS = 86_400_000
MAX_TOKENS = MAX_TOKEN_COUNT
MAX_LEDGER_ENTRIES = 1024
_MESSAGE = 'run budget invalid; details omitted'


def _invalid():
    raise LLMToolError(_MESSAGE)


def _strict(method):
    """Re-raise shared helper failures with this module's fixed message."""
    @wraps(method)
    def checked(*args):
        try:
            return method(*args)
        except LLMToolError:
            raise LLMToolError(_MESSAGE) from None
    return checked


def _optional_count(value, maximum=MAX_TOKENS):
    if value is not None:
        _integer(value, maximum)


class CallPhase(str, Enum):
    PREPARATION = 'PREPARATION'
    PRIMARY = 'PRIMARY'
    RETRY = 'RETRY'
    REPAIR = 'REPAIR'
    ESCALATION = 'ESCALATION'
    REVIEW = 'REVIEW'


class BudgetDimension(str, Enum):
    PROVIDER_CALLS = 'PROVIDER_CALLS'
    REPAIR_ATTEMPTS = 'REPAIR_ATTEMPTS'
    WALL_CLOCK = 'WALL_CLOCK'
    INPUT_TOKENS = 'INPUT_TOKENS'
    OUTPUT_TOKENS = 'OUTPUT_TOKENS'


class BudgetStatus(str, Enum):
    WITHIN = 'WITHIN'
    EXCEEDED = 'EXCEEDED'
    UNKNOWN = 'UNKNOWN'


class BudgetCode(str, Enum):
    WITHIN_BUDGET = 'WITHIN_BUDGET'
    CALL_LIMIT_REACHED = 'CALL_LIMIT_REACHED'
    REPAIR_LIMIT_REACHED = 'REPAIR_LIMIT_REACHED'
    WALL_CLOCK_EXCEEDED = 'WALL_CLOCK_EXCEEDED'
    TOKEN_BUDGET_EXCEEDED = 'TOKEN_BUDGET_EXCEEDED'
    USAGE_UNKNOWN = 'USAGE_UNKNOWN'
    ELAPSED_UNKNOWN = 'ELAPSED_UNKNOWN'
    REPEATED_FAILURE = 'REPEATED_FAILURE'


@dataclass(frozen=True)
class RunBudget(ContractValue):
    schema_version: int
    max_provider_calls: int
    max_repair_attempts: int
    wall_clock_ms: int
    max_input_tokens: int
    max_output_tokens: int

    @_strict
    def __post_init__(self):
        _version(self.schema_version)
        _integer(self.max_provider_calls, MAX_PROVIDER_CALLS, 1)
        _integer(self.max_repair_attempts, MAX_REPAIR_ATTEMPTS)
        _integer(self.wall_clock_ms, MAX_WALL_CLOCK_MS, 1)
        _integer(self.max_input_tokens, MAX_TOKENS)
        _integer(self.max_output_tokens, MAX_TOKENS)


@dataclass(frozen=True)
class CallUsage(ContractValue):
    phase: CallPhase
    input_tokens: Optional[int]
    output_tokens: Optional[int]

    @_strict
    def __post_init__(self):
        _typed(self.phase, CallPhase)
        _optional_count(self.input_tokens)
        _optional_count(self.output_tokens)
        if (self.input_tokens is None) != (self.output_tokens is None):
            _invalid()

    @property
    def known(self):
        return self.input_tokens is not None


def usage_from_provider(phase, metadata):
    """Map one validated provider usage triplet; anything else is unknown, never 0."""
    try:
        usage = validate_provider_usage(metadata)
    except (ValueError, TypeError):
        return CallUsage(phase, None, None)
    return CallUsage(phase, usage['prompt_tokens'], usage['completion_tokens'])


@dataclass(frozen=True)
class UsageLedger(ContractValue):
    calls: tuple
    repair_attempts: int
    elapsed_ms: Optional[int]

    @_strict
    def __post_init__(self):
        object.__setattr__(self, 'calls', _collection(
            self.calls, CallUsage, MAX_LEDGER_ENTRIES, 0))
        _integer(self.repair_attempts, MAX_LEDGER_ENTRIES)
        if self.elapsed_ms is not None:
            _integer(self.elapsed_ms)


@dataclass(frozen=True)
class DimensionAssessment(ContractValue):
    dimension: BudgetDimension
    status: BudgetStatus
    used: Optional[int]
    limit: int
    unknown_calls: int

    @_strict
    def __post_init__(self):
        _typed(self.dimension, BudgetDimension)
        _typed(self.status, BudgetStatus)
        if self.used is not None:
            _integer(self.used)
        _integer(self.limit)
        _integer(self.unknown_calls, MAX_LEDGER_ENTRIES)


def _aggregate(statuses):
    for status in (BudgetStatus.EXCEEDED, BudgetStatus.UNKNOWN):
        if status in statuses:
            return status
    return BudgetStatus.WITHIN


@dataclass(frozen=True)
class BudgetAssessment(ContractValue):
    status: BudgetStatus
    dimensions: tuple

    @_strict
    def __post_init__(self):
        _typed(self.status, BudgetStatus)
        dims = _collection(self.dimensions, DimensionAssessment, 5, 5)
        if (tuple(d.dimension for d in dims) != tuple(BudgetDimension) or
                self.status is not _aggregate([d.status for d in dims])):
            _invalid()
        object.__setattr__(self, 'dimensions', dims)

    @property
    def within_budget(self):
        return self.status is BudgetStatus.WITHIN

    def dimension(self, which):
        return self.dimensions[tuple(BudgetDimension).index(which)]


def _check(budget, ledger):
    if type(budget) is not RunBudget or type(ledger) is not UsageLedger:
        _invalid()


def _count(dimension, used, limit):
    status = BudgetStatus.EXCEEDED if used > limit else BudgetStatus.WITHIN
    return DimensionAssessment(dimension, status, used, limit, 0)


def _tokens(dimension, values, limit):
    known = [v for v in values if v is not None]
    unknown = len(values) - len(known)
    total = sum(known)
    if total > limit:
        status = BudgetStatus.EXCEEDED  # provable even when other calls are unknown
    elif unknown:
        status = BudgetStatus.UNKNOWN
    else:
        status = BudgetStatus.WITHIN
    # used is None only when every recorded call is unknown; with unknowns it is a
    # partial subtotal flagged by unknown_calls; an empty ledger is a known 0.
    used = total if known or not values else None
    return DimensionAssessment(dimension, status, used, limit, unknown)


def assess_budget(budget, ledger):
    _check(budget, ledger)
    elapsed = ledger.elapsed_ms
    if elapsed is None:
        wall = BudgetStatus.UNKNOWN
    else:
        wall = BudgetStatus.EXCEEDED if elapsed > budget.wall_clock_ms else BudgetStatus.WITHIN
    dims = (
        _count(BudgetDimension.PROVIDER_CALLS, len(ledger.calls), budget.max_provider_calls),
        _count(BudgetDimension.REPAIR_ATTEMPTS, ledger.repair_attempts,
               budget.max_repair_attempts),
        DimensionAssessment(BudgetDimension.WALL_CLOCK, wall, elapsed, budget.wall_clock_ms, 0),
        _tokens(BudgetDimension.INPUT_TOKENS, [c.input_tokens for c in ledger.calls],
                budget.max_input_tokens),
        _tokens(BudgetDimension.OUTPUT_TOKENS, [c.output_tokens for c in ledger.calls],
                budget.max_output_tokens),
    )
    return BudgetAssessment(_aggregate([d.status for d in dims]), dims)


def admit_next(budget, ledger, phase):
    """Pure predicate for one more call in phase; returns a code and grants nothing."""
    _check(budget, ledger)
    if type(phase) is not CallPhase:
        _invalid()
    if len(ledger.calls) + 1 > budget.max_provider_calls:
        return BudgetCode.CALL_LIMIT_REACHED
    # A ledger already over its repair cap is denied for every phase.
    pending = 1 if phase is CallPhase.REPAIR else 0
    if ledger.repair_attempts + pending > budget.max_repair_attempts:
        return BudgetCode.REPAIR_LIMIT_REACHED
    assessment = assess_budget(budget, ledger)
    wall = assessment.dimension(BudgetDimension.WALL_CLOCK)
    tokens = [assessment.dimension(d) for d in
              (BudgetDimension.INPUT_TOKENS, BudgetDimension.OUTPUT_TOKENS)]
    # Prospective, unlike assess_budget: a limit that is merely reached (used ==
    # limit) leaves no capacity for another call, so it is denied as well.
    if _exhausted(wall):
        return BudgetCode.WALL_CLOCK_EXCEEDED
    if any(_exhausted(d) for d in tokens):
        return BudgetCode.TOKEN_BUDGET_EXCEEDED
    if wall.status is BudgetStatus.UNKNOWN:
        return BudgetCode.ELAPSED_UNKNOWN
    if any(d.status is BudgetStatus.UNKNOWN for d in tokens):
        return BudgetCode.USAGE_UNKNOWN
    return BudgetCode.WITHIN_BUDGET


def _exhausted(dimension):
    """No capacity left: a zero limit, or known (possibly partial) use at the limit.

    Unknown usage is >= 0, so both are provable even when some calls are unknown;
    otherwise unknown stays unknown and is never read as zero.
    """
    return dimension.limit == 0 or (dimension.used is not None and
                                    dimension.used >= dimension.limit)


@dataclass(frozen=True)
class AttemptFingerprint(ContractValue):
    action_digest: str
    failure_code: str

    @_strict
    def __post_init__(self):
        _hex(self.action_digest)
        _identifier(self.failure_code)


@_strict
def detect_repeated_failure(attempts):
    """Index of the first attempt whose exact (digest, code) pair occurred earlier.

    Comparison rule: case-sensitive string equality of both fields; nothing else.
    """
    seen = set()
    for index, attempt in enumerate(_collection(attempts, AttemptFingerprint,
                                                MAX_LEDGER_ENTRIES, 0)):
        key = (attempt.action_digest, attempt.failure_code)
        if key in seen:
            return index
        seen.add(key)
    return None
