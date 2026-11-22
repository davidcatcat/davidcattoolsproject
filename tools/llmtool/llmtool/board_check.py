"""Read-only PM board reconciliation for ``llmtool board-check`` (#763).

Every pm-board.json row is classified from evidence only: the TaskStore record,
``task_advance.decide``, GitHub reads, the registered D6 and live processes.
Row text, row status and ``updated_at`` are never read, so they cannot change a
state. The check classifies and reports; it never dispatches, writes a Task,
or changes GitHub. GitHub and process reads go through injected adapters, and
any failed read yields UNKNOWN, never a clean result.

A live state needs attempt-bound identity: a trusted launch PID and start time,
or a verified native session seen on the process. A matching cwd only supports
that identity. Evidence that can neither prove nor rule out the attempt, such as
a start time inside the same whole second as the dispatch, is UNKNOWN.

Exit status: 0 clean, 2 when any row is ACTIONABLE (idle work with a next step)
or ANOMALY, 1 when the board is unreadable or any evidence read failed and
nothing is flagged. A failed read stays UNKNOWN on the row it affects; it does
not weaken a finding proven by other evidence.
"""

import re
from datetime import timedelta

BOARD_SCHEMA_VERSION = 1
MAX_BOARD_BYTES = 256 * 1024
MAX_GITHUB_PAGES = 10

RUNNING = 'RUNNING'
WAITING_CI = 'WAITING_CI'
WAITING_REVIEW = 'WAITING_REVIEW'
WAITING_OWNER = 'WAITING_OWNER'
BLOCKED = 'BLOCKED'
ACTIONABLE = 'ACTIONABLE'
ANOMALY = 'ANOMALY'
UNKNOWN = 'UNKNOWN'
DONE = 'DONE'
CLAIMED = 'CLAIMED'
FLAGGED_STATES = frozenset((ACTIONABLE, ANOMALY))

LEDGER_SCHEMA_VERSION = 1
MAX_LEDGER_BYTES = 256 * 1024
MAX_LEDGER_CLAIMS = 1024

RESERVATION_TTL = timedelta(minutes=15)
# Tolerate slight clock skew across cooperating PM hosts/processes.
_CLAIM_CLOCK_SKEW = timedelta(seconds=60)
LEDGER_LOCK_TIMEOUT_SECONDS = 10
CLAIM_REFUSED_EXIT = 3
_CLAIM_FIELDS = frozenset(('holder', 'row', 'claimed_at', 'task_id', 'bound_at'))
_HOLDER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}\Z')
_UTC_SECONDS = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z')

# Developer routes that take a registered D6 writer. Company gw only proposes
# patches without file or Git authority, so it never counts as free capacity
# for D6 work.
DEVELOPER_ROUTES = ('claude-developer', 'opencode-developer', 'agy')
# Policy results that are a legal authorization wait, not a policy fault.
_AUTHORIZATION_WAIT_CODES = frozenset(('OWNER_PAUSED', 'OWNER_ASSIGNMENT_REQUIRED'))
_UNCHECKABLE_WORKER_CODES = frozenset(('WORKER_UNKNOWN', 'WORKER_KEY_INVALID'))
_DISPATCH_ACTIONS = frozenset((
    'DISPATCH', 'RETRY', 'REASSIGN', 'RECOVERY', 'REPAIR', 'TIMEOUT_CONTINUATION'))
# decide() steps whose remaining gate can be an exact-head check still running.
_CI_WAIT_STEPS = frozenset(('DISPATCH_REVIEW', 'RUN_TASK_COMPLETE', 'READY_FOR_INTEGRATION'))
# derive_state values after which a Task can no longer own a live attempt.
_TERMINAL_TASK_STATES = frozenset(('COMPLETED', 'ABANDONED', 'FAILED'))
# ``ps -o lstart`` reports whole seconds, so a start is an interval, not an instant.
_PROCESS_START_RESOLUTION = timedelta(seconds=1)
_LSTART_FORMAT = '%a %b %d %H:%M:%S %Y'

_TASK_ID = re.compile(r'[0-9a-f]{32}\Z')
_SHA = re.compile(r'[0-9a-f]{40}\Z')
_CODE = re.compile(r'[A-Z0-9_]{1,64}\Z')
_UTC = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3}|\.\d{6})?(?:Z|\+00:00)\Z')
_OWNER_REF = re.compile(
    r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(?:issues|pull)/'
    r'([1-9][0-9]{0,6})(?:#issuecomment-[1-9][0-9]*)?\Z')
_ACTIVITY = re.compile(r'<!--\s*activity:([^<>]{0,512}?)-->')
_ARGV_SESSION = re.compile(
    r'(?:^|\s)(?:--session-id|--resume|resume)[=\s]+([A-Za-z0-9_-]{8,128})(?=\s|$)')


class _Unknown(Exception):
    """Evidence could not be read; the affected row is UNKNOWN."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code if type(code) is str and _CODE.fullmatch(code) else 'EVIDENCE_UNAVAILABLE'


class BoardUnreadable(Exception):
    """The board file is missing, unreadable or not schema_version 1."""


class LedgerUnreadable(Exception):
    """The ledger file is missing, unreadable or not schema_version 1."""


class ClaimRefused(Exception):
    """A claim operation was refused; carries a machine-readable code."""

    def __init__(self, code, *args):
        super().__init__(code, *args)
        self.code = code


from .board_check_part1 import (  # noqa: E402, F401
    BoardLedger,
    _seconds,
    _valid_claim,
    claim_live,
    claim_row,
    default_ledger_path,
)
from .board_check_part2 import (  # noqa: E402, F401
    _Context,
    _attempt_identity,
    _attempt_process,
    _claims,
    _cover_record,
    _dispatched_at,
    _executor_task,
    _issue_marker_status,
    _no_process,
    _owner_wait,
    _pending_checks,
    _pr_number,
)
from .board_check_part3 import (  # noqa: E402, F401
    _capacity,
    _classify_pr,
    _classify_repair,
    _classify_row,
    _classify_task,
    _finish,
    _gate,
    _label,
    _merged,
    _related,
    _route_occupancy,
    _row_done,
    _row_gates,
    _row_label,
    _row_problem,
    _unlisted,
    check_board,
)
from .board_check_part4 import (  # noqa: E402, F401
    _identity_match,
    _lstart,
    _ps_snapshot,
    _reject_constant,
    _stamp,
    _start_order,
    _utc,
    add_board_check_parser,
    default_board_path,
    format_result,
    github_reader,
    list_processes,
    parse_predicate,
    read_board,
    run_board_check,
)
