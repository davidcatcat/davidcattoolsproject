"""Developer role-timeout classifier and logical attempt count (#1019 A11 row 41).

PR 1a and PR 1b of docs/design/a11-row41-timeout-retry-1019.md section 7.
Pure: no I/O, no clock, no environment, and no import of task_store,
task_advance or worker_dispatch. worker_dispatch uses the route table and
count_timeout_attempts (the monotone count of design section 3.5); advance
and drive are not wired yet.

Route timeout codes, confirmed at source 65f5993137007e26a5d14dabf444c7e99b5fcaaf:

* developer_routes/base.py ``run_route`` raises
  ``RouteError(_route_code(route, 'TIMEOUT'))`` when the runner raises
  ``TimeoutError`` or ``subprocess.TimeoutExpired`` (lines 878-883), and
  ``_route_code(route, suffix)`` is ``route.name.upper() + '_' + suffix``
  (lines 385-386). ``RouteError`` keeps a code only if it matches
  ``[A-Z0-9_]{1,64}``; any other code becomes ``ROUTE_ERROR`` (lines 56-57).
* opencode_direct_developer.py rejects with the literal ``OPENCODE_TIMEOUT``
  for the run deadline (line 905), the version probe deadline (line 747) and
  a process that does not exit by its deadline (line 592).

| Route    | Timeout code       | Source                                       |
| -------- | ------------------ | -------------------------------------------- |
| claude   | CLAUDE_TIMEOUT     | base.py _route_code(route, 'TIMEOUT')        |
| agy      | AGY_TIMEOUT        | base.py _route_code(route, 'TIMEOUT')        |
| gw-agent | GW_AGENT_TIMEOUT   | base.py _route_code(route, 'TIMEOUT')        |
| opencode | OPENCODE_TIMEOUT   | opencode_direct_developer.py OPENCODE_TIMEOUT |

The route name ``agy`` is confirmed by base.py line 1001. The claude and
gw-agent adapters were not in this Task's read paths: their names are taken
as ``claude`` and ``gw_agent``, the names that ``_route_timeout_limits``
(base.py lines 240-244, ``name.lower().replace('_', '-')``) maps to the
registry keys ``claude`` and ``gw-agent``. A name ``gw-agent`` would give no
distinct code (``-`` fails the RouteError pattern). UNVERIFIED until the PR
that wires this module reads those adapters.

These strings replace the proposed ``*_DEVELOPER_TIMEOUT`` names in the
design section 3.1 table. Matching is exact: no prefix, pattern, substring
or case folding. Quota, free-limit, auth, crash, step-limit
(``OPENCODE_STEPS_EXHAUSTED``) and Codex ``CODEX_TIMEOUT_WITH_CHANGES``
codes never equal a table code, so they classify ``NOT_TIMEOUT``.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from types import MappingProxyType

from .errors import LLMToolError

ROLE_TIMEOUT = 'ROLE_TIMEOUT'
NOT_TIMEOUT = 'NOT_TIMEOUT'

ROUTE_TIMEOUT_CODES = MappingProxyType({
    'claude': 'CLAUDE_TIMEOUT',
    'agy': 'AGY_TIMEOUT',
    'gw-agent': 'GW_AGENT_TIMEOUT',
    'opencode': 'OPENCODE_TIMEOUT',
})
# Every route's exact timeout code, for checks that are not route-specific.
ROLE_TIMEOUT_CODES = frozenset(ROUTE_TIMEOUT_CODES.values())

# Audit actions and retry phases (design section 3.3).
ROLE_TIMEOUT_RETRY = 'ROLE_TIMEOUT_RETRY'
TIMEOUT_CONTINUATION = 'TIMEOUT_CONTINUATION'
PHASE_TRANSITION = 'TRANSITION'
PHASE_ADAPTER = 'ADAPTER'
TIMEOUT_ACTIONS = frozenset((ROLE_TIMEOUT_RETRY, TIMEOUT_CONTINUATION))
# Frozen copy of worker_dispatch.AUDIT_ACTIONS without the two timeout actions
# (design section 3.5 step 1). Kept here so this module does not import
# worker_dispatch; a test checks the two sets agree. A new audit action that is
# missing here counts 1 (fails closed).
UNRELATED_ACTIONS = frozenset((
    'DISPATCH', 'RETRY', 'REASSIGN', 'RECOVERY', 'REPAIR',
    'REVIEWER_HANDOFF', 'REVIEWER_RETRY', 'COMMITTED_RESULT_CARRY',
))
AUDIT_DECISIONS = frozenset(('ALLOWED', 'BLOCKED'))

# Fixed refusal codes of this module.
ROUTE_UNKNOWN = 'ROLE_TIMEOUT_ROUTE_UNKNOWN'
INPUT_INVALID = 'ROLE_TIMEOUT_INPUT_INVALID'
AUDIT_INVALID = 'ROLE_TIMEOUT_AUDIT_INVALID'


class RoleTimeoutError(LLMToolError):
    """Fixed-code refusal; the message never carries input values."""

    def __init__(self, code: str):
        super().__init__('Role timeout classification refused; details omitted')
        self.code = code


def classify(route, error_code):
    """Return ROLE_TIMEOUT only for route's exact timeout code, else NOT_TIMEOUT.

    route is a Developer route id from ROUTE_TIMEOUT_CODES (not a worker
    key). A non-str route or error_code (str subclasses included) raises
    RoleTimeoutError(INPUT_INVALID); an unknown route raises
    RoleTimeoutError(ROUTE_UNKNOWN). No other exception is raised.
    """
    if type(route) is not str or type(error_code) is not str:
        raise RoleTimeoutError(INPUT_INVALID)
    expected = ROUTE_TIMEOUT_CODES.get(route)
    if expected is None:
        raise RoleTimeoutError(ROUTE_UNKNOWN)
    return ROLE_TIMEOUT if error_code == expected else NOT_TIMEOUT


# Entry classes of design section 3.5 steps 1 and 2.
_UNRELATED = 'UNRELATED'
_OTHER = 'OTHER'
_TRANSITION = 'TRANSITION'
_ADAPTER = 'ADAPTER'
_HEX = {size: re.compile('[0-9a-f]{%d}' % size) for size in (32, 40, 64)}
# Fields an adapter must share with its transition to complete the pair.
_PAIR_FIELDS = ('action', 'worker_key', 'assignment_id', 'source_sha', 'contract_digest')


def _hex(value, size):
    """True only for a str of exactly type str that is size lower-case hex digits."""
    return type(value) is str and _HEX[size].fullmatch(value) is not None


def _classify_entry(entry):
    """Return (class, identity) for one audit entry (design section 3.5 steps 1-2).

    identity is the attempt_id of a TRANSITION, the authorization_id of an
    ADAPTER, and None for UNRELATED and OTHER entries.
    """
    if not isinstance(entry, Mapping):
        return _OTHER, None
    action = entry.get('action')
    if type(action) is not str:
        return _OTHER, None
    if action in UNRELATED_ACTIONS:
        return _UNRELATED, None
    if action not in TIMEOUT_ACTIONS:
        return _OTHER, None  # every unknown name, timeout-like or not, counts
    attempt_id = entry.get('attempt_id')
    worker_key = entry.get('worker_key')
    decision = entry.get('decision')
    if not (_hex(attempt_id, 32) and _hex(entry.get('assignment_id'), 32) and
            _hex(entry.get('source_sha'), 40) and _hex(entry.get('contract_digest'), 64) and
            type(worker_key) is str and worker_key != '' and
            type(decision) is str and decision in AUDIT_DECISIONS):
        return _OTHER, None
    has_authorization = 'authorization_id' in entry
    if action == ROLE_TIMEOUT_RETRY:
        phase = entry.get('retry_phase')
        if type(phase) is not str or phase not in (PHASE_TRANSITION, PHASE_ADAPTER):
            return _OTHER, None
        if has_authorization != (phase == PHASE_ADAPTER):
            return _OTHER, None
    elif 'retry_phase' in entry:
        return _OTHER, None  # TIMEOUT_CONTINUATION never carries a phase
    if not has_authorization:
        return _TRANSITION, attempt_id
    authorization_id = entry['authorization_id']
    if not _hex(authorization_id, 32) or authorization_id == attempt_id:
        return _OTHER, None
    return _ADAPTER, authorization_id


def _completes_pair(transition, adapter):
    return all(transition[field] == adapter[field] for field in _PAIR_FIELDS)


def count_timeout_attempts(entries):
    """Count logical timeout retry attempts in one Task record's audit.

    entries is the record's dispatch audit, a list or tuple of entries. The
    count is the monotone count of design section 3.5: every
    ROLE_TIMEOUT_RETRY, TIMEOUT_CONTINUATION and unclassifiable entry counts
    1, except that for each completed identity (an ALLOWED transition and a
    later adapter naming it, with the same action, worker key and binding)
    exactly one adapter counts 0. Exact unrelated audit actions count 0.
    Every decision, worker key and assignment counts. Adding, duplicating or
    altering an entry into one without a valid identity never lowers the
    count. A non-list, non-tuple audit raises RoleTimeoutError(AUDIT_INVALID).
    The input is not modified.
    """
    if type(entries) not in (list, tuple):
        raise RoleTimeoutError(AUDIT_INVALID)
    counted = 0
    transitions = {}  # identity -> earlier qualifying (ALLOWED) transition entries
    completed = set()
    for entry in entries:
        kind, identity = _classify_entry(entry)
        if kind == _UNRELATED:
            continue
        counted += 1
        if kind == _TRANSITION:
            if entry['decision'] == 'ALLOWED':
                transitions.setdefault(identity, []).append(entry)
        elif kind == _ADAPTER and identity not in completed:
            if any(_completes_pair(transition, entry)
                   for transition in transitions.get(identity, ())):
                completed.add(identity)
    return counted - len(completed)
