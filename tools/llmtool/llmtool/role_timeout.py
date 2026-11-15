"""Developer role-timeout classifier and logical attempt count (#1019 A11 row 41).

PR 1a of docs/design/a11-row41-timeout-retry-1019.md section 7. Pure: no
I/O, no clock, no environment, and no import of task_store, task_advance or
worker_dispatch. Not wired: no caller uses this module yet.

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

# Audit actions and retry phases (design section 3.3).
ROLE_TIMEOUT_RETRY = 'ROLE_TIMEOUT_RETRY'
TIMEOUT_CONTINUATION = 'TIMEOUT_CONTINUATION'
PHASE_TRANSITION = 'TRANSITION'
PHASE_ADAPTER = 'ADAPTER'

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


def _has_authorization_id(entry):
    value = entry.get('authorization_id')
    return type(value) is str and value != ''


def _entry_count(entry):
    """Logical attempts for one audit entry (design section 3.3 count rule)."""
    if not isinstance(entry, Mapping):
        return 1  # cannot be classified: counts as used
    action = entry.get('action')
    if type(action) is not str:
        return 1  # cannot be classified: counts as used
    if action == ROLE_TIMEOUT_RETRY:
        # TRANSITION, a missing or unknown phase, and TRANSITION with an
        # authorization_id all count 1; only an ADAPTER entry counts 0.
        phase = entry.get('retry_phase')
        return 0 if type(phase) is str and phase == PHASE_ADAPTER else 1
    if action == TIMEOUT_CONTINUATION:
        # Codex: the transition entry has no authorization_id; the adapter
        # entry has one. A missing or malformed value counts as a transition.
        return 0 if _has_authorization_id(entry) else 1
    return 0


def count_timeout_attempts(entries):
    """Count logical timeout retry attempts in one Task record's audit.

    entries is the record's dispatch audit, a list or tuple of entries.
    Every entry counts, whatever its decision, worker key or assignment.
    One retry writes a transition entry and an adapter entry; only the
    transition entry counts, so each attempt counts once. An entry that
    cannot be classified counts 1, never 0. A non-list, non-tuple audit
    raises RoleTimeoutError(AUDIT_INVALID). The input is not modified.
    """
    if type(entries) not in (list, tuple):
        raise RoleTimeoutError(AUDIT_INVALID)
    return sum(_entry_count(entry) for entry in entries)
