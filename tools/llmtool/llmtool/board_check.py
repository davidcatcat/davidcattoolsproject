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

import contextlib
import fcntl
import json
import os
import re
import stat
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import task_advance, task_status, worker_dispatch
from .task_store import TaskStore

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
_HOLDER = re.compile(r'[A-Za-z0-9_.-]{1,64}\Z')
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


def default_board_path():
    return Path.home() / '.local' / 'state' / 'llmtool' / 'pm-board.json'


def _reject_constant(_):
    raise ValueError('non-finite number')


def read_board(path):
    """Return the board rows; any doubt about the file raises BoardUnreadable."""
    try:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BOARD_BYTES:
                raise BoardUnreadable('board is not a bounded regular file')
            chunks, remaining = [], MAX_BOARD_BYTES + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(fd)
        raw = b''.join(chunks)
        if len(raw) > MAX_BOARD_BYTES:
            raise BoardUnreadable('board exceeds size limit')
        value = json.loads(raw.decode('utf-8'),
                           object_pairs_hook=worker_dispatch._unique_pairs,
                           parse_constant=_reject_constant)
    except BoardUnreadable:
        raise
    except (OSError, UnicodeError, ValueError, TypeError):
        raise BoardUnreadable('board unreadable') from None
    if (type(value) is not dict or type(value.get('schema_version')) is not int or
            value['schema_version'] != BOARD_SCHEMA_VERSION or
            type(value.get('rows')) is not list):
        raise BoardUnreadable('board is not schema_version 1 with a rows list')
    return value['rows']


def _utc(value):
    if type(value) is not str or _UTC.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def _interval(value):
    """Return the [start, end) a recorded UTC stamp covers at its own precision."""
    parsed = _utc(value)
    if parsed is None:
        return None
    digits = len(re.sub(r'(?:Z|\+00:00)\Z', '', value).partition('.')[2])
    return parsed, parsed + timedelta(seconds=10 ** -digits)


def _start_order(started_at, since):
    """Place a process start against an event: 'before', 'after' or 'overlap'.

    Equal whole seconds overlap, so they never establish an order.
    """
    if started_at + _PROCESS_START_RESOLUTION <= since[0]:
        return 'before'
    return 'after' if started_at >= since[1] else 'overlap'


def _lstart(text):
    try:
        return datetime.strptime(text, _LSTART_FORMAT)
    except ValueError:
        return None


def _identity_match(item, identity):
    """True or False for one process; None when its start cannot be compared."""
    if 'pid' in identity:
        if item['pid'] != identity['pid']:
            return False
        recorded = ' '.join(identity['start'].split())
        if item['start_time'] == recorded:
            return True
        # A comparable different start means the PID was reused.
        return False if _lstart(recorded) is not None else None
    return identity.get('session') in item.get('native_session_ids', ())


def _stamp(value):
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_predicate(text):
    """Parse one checkable predicate; free text returns None."""
    if type(text) is not str:
        return None
    if text == 'capacity':
        return ('capacity', None)
    kind, separator, value = text.partition(':')
    if not separator:
        return None
    if kind in ('pr_merged', 'issue_closed') and re.fullmatch(r'[1-9][0-9]{0,6}', value):
        return (kind, int(value))
    if kind == 'dispatch_denied' and re.fullmatch(r'[a-z][a-z0-9-]{0,63}', value):
        return (kind, value)
    if kind == 'lease' and _TASK_ID.fullmatch(value):
        return (kind, value)
    if kind == 'time' and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value):
        parsed = _utc(value)
        return (kind, parsed) if parsed is not None else None
    return None


class LedgerUnreadable(Exception):
    """The ledger file is missing, unreadable or not schema_version 1."""


class ClaimRefused(Exception):
    """A claim operation was refused; carries a machine-readable code."""

    def __init__(self, code, *args):
        super().__init__(code, *args)
        self.code = code


def default_ledger_path():
    return Path.home() / '.local' / 'state' / 'llmtool' / 'pm-claims.json'


def _seconds(dt):
    if isinstance(dt, datetime):
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if type(dt) is not str or _UTC_SECONDS.fullmatch(dt) is None:
        return None
    try:
        return datetime.fromisoformat(dt.replace('Z', '+00:00'))
    except ValueError:
        return None


def _valid_claim(claim):
    if type(claim) is not dict:
        return False
    if set(claim) != _CLAIM_FIELDS:
        return False
    holder = claim.get('holder')
    if type(holder) is not str or _HOLDER.fullmatch(holder) is None:
        return False
    row = claim.get('row')
    if type(row) is not str or not (1 <= len(row) <= 512):
        return False
    claimed_at = claim.get('claimed_at')
    if type(claimed_at) is not str or _UTC_SECONDS.fullmatch(claimed_at) is None or _seconds(claimed_at) is None:
        return False
    task_id = claim.get('task_id')
    bound_at = claim.get('bound_at')
    if task_id is None:
        if bound_at is not None:
            return False
    else:
        if type(task_id) is not str or _TASK_ID.fullmatch(task_id) is None:
            return False
        if type(bound_at) is not str or _UTC_SECONDS.fullmatch(bound_at) is None or _seconds(bound_at) is None:
            return False
    return True


class BoardLedger:
    """Cooperative process-level claim ledger for pm-board reconciliation."""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_ledger_path()
        self.lock_path = self.path.with_name(self.path.name + '.lock')

    def read(self):
        try:
            st = os.lstat(self.path)
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise LedgerUnreadable('ledger unreadable') from exc

        if stat.S_ISLNK(st.st_mode):
            raise LedgerUnreadable('ledger is a symlink')
        if not stat.S_ISREG(st.st_mode):
            raise LedgerUnreadable('ledger is not a regular file')
        if stat.S_IMODE(st.st_mode) != 0o600:
            raise LedgerUnreadable('ledger permissions must be 0600')
        if st.st_size > MAX_LEDGER_BYTES:
            raise LedgerUnreadable('ledger exceeds size limit')

        try:
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
            fd = os.open(self.path, flags)
            try:
                info = os.fstat(fd)
                if stat.S_ISLNK(info.st_mode):
                    raise LedgerUnreadable('ledger is a symlink')
                if not stat.S_ISREG(info.st_mode):
                    raise LedgerUnreadable('ledger is not a regular file')
                if stat.S_IMODE(info.st_mode) != 0o600:
                    raise LedgerUnreadable('ledger permissions must be 0600')
                if info.st_size > MAX_LEDGER_BYTES:
                    raise LedgerUnreadable('ledger exceeds size limit')
                chunks, remaining = [], MAX_LEDGER_BYTES + 1
                while remaining:
                    chunk = os.read(fd, min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
            finally:
                os.close(fd)
            raw = b''.join(chunks)
            if len(raw) > MAX_LEDGER_BYTES:
                raise LedgerUnreadable('ledger exceeds size limit')
            value = json.loads(raw.decode('utf-8'),
                               object_pairs_hook=worker_dispatch._unique_pairs,
                               parse_constant=_reject_constant)
        except LedgerUnreadable:
            raise
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise LedgerUnreadable('ledger unreadable') from exc

        if (type(value) is not dict or type(value.get('schema_version')) is not int or
                value['schema_version'] != LEDGER_SCHEMA_VERSION or
                type(value.get('claims')) is not list):
            raise LedgerUnreadable('ledger is not schema_version 1 with a claims list')

        claims = value['claims']
        if len(claims) > MAX_LEDGER_CLAIMS:
            raise LedgerUnreadable('ledger claims exceed limit')
        for claim in claims:
            if not _valid_claim(claim):
                raise LedgerUnreadable('invalid claim in ledger')
        return claims

    @contextlib.contextmanager
    def locked(self):
        parent = self.path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            st = os.stat(parent)
            if st.st_mode & 0o022:
                raise ClaimRefused('LEDGER_UNAVAILABLE')
        except ClaimRefused:
            raise
        except OSError:
            raise ClaimRefused('LEDGER_UNAVAILABLE')

        timeout = LEDGER_LOCK_TIMEOUT_SECONDS
        deadline = time.time() + timeout
        try:
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            raise ClaimRefused('LEDGER_UNAVAILABLE')
        try:
            try:
                os.chmod(self.lock_path, 0o600)
            except OSError:
                pass
            acquired = False
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, OSError):
                    if time.time() >= deadline:
                        break
                    time.sleep(0.02)
            if not acquired:
                raise ClaimRefused('LEDGER_BUSY')
            try:
                yield self
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)

    def write(self, claims):
        if len(claims) > MAX_LEDGER_CLAIMS:
            raise ClaimRefused('LEDGER_FULL')
        for claim in claims:
            if not _valid_claim(claim):
                raise ClaimRefused('LEDGER_CLAIM_INVALID')

        parent = self.path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            st = os.stat(parent)
            if st.st_mode & 0o022:
                raise ClaimRefused('LEDGER_UNAVAILABLE')
        except ClaimRefused:
            raise
        except OSError:
            raise ClaimRefused('LEDGER_UNAVAILABLE')

        payload = {
            'schema_version': LEDGER_SCHEMA_VERSION,
            'claims': list(claims),
        }
        data = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode('utf-8')
        if len(data) > MAX_LEDGER_BYTES:
            raise ClaimRefused('LEDGER_FULL')

        tmp_path = self.path.with_name(f'{self.path.name}.tmp.{uuid.uuid4().hex}')
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(tmp_path, flags, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            os.replace(tmp_path, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass


class _Context:
    """Cached, read-only evidence for one board check."""

    def __init__(self, *, store, github_get, list_processes, read_d6, evaluate, now):
        self.store = store
        self._github_get = github_get
        self._list_processes = list_processes
        self._read_d6 = read_d6
        self._evaluate = evaluate
        self.now = now
        self._ids = None
        self._records = {}
        self._processes = None
        self._assignments = None
        self._issue_markers = {}
        self.covered_prs = set()
        self.covered_tasks = set()

    def get(self, path):
        try:
            return self._github_get(path)
        except Exception:
            raise _Unknown('GITHUB_READ_FAILED') from None

    def get_pages(self, path):
        """Read a bounded paginated GitHub list; truncation is UNKNOWN."""
        items = []
        separator = '&' if '?' in path else '?'
        for page in range(1, MAX_GITHUB_PAGES + 1):
            value = self.get(f'{path}{separator}per_page=100&page={page}')
            if type(value) is not list:
                raise _Unknown('GITHUB_READ_INVALID')
            items.extend(value)
            if len(value) < 100:
                return items
        raise _Unknown('GITHUB_LIST_TRUNCATED')

    def pull(self, number):
        value = self.get(f'pulls/{number}')
        head = value.get('head') if type(value) is dict else None
        if (type(value) is not dict or type(value.get('merged')) is not bool or
                value.get('state') not in ('open', 'closed') or type(head) is not dict or
                type(head.get('sha')) is not str or _SHA.fullmatch(head['sha']) is None):
            raise _Unknown('PR_UNAVAILABLE')
        return value

    def evaluate(self, worker, owner_ref=None, task_id=None):
        try:
            return self._evaluate(worker, owner_ref, task_id)
        except Exception:
            raise _Unknown('DISPATCH_POLICY_UNAVAILABLE') from None

    def task_ids(self):
        if self._ids is None:
            try:
                ids = self.store.task_ids()
            except Exception:
                raise _Unknown('TASK_STORE_UNAVAILABLE') from None
            self._ids = frozenset(item for item in ids if type(item) is str)
        return self._ids

    def record(self, task_id):
        """Return the Task record, None when no such Task exists; failures are UNKNOWN."""
        if task_id not in self._records:
            if task_id not in self.task_ids():
                self._records[task_id] = None
            else:
                try:
                    value = self.store.get(task_id)
                except Exception:
                    raise _Unknown('TASK_RECORD_UNREADABLE') from None
                if type(value) is not dict:
                    raise _Unknown('TASK_RECORD_UNREADABLE')
                self._records[task_id] = value
        return self._records[task_id]

    def d6_root(self, record, d6_task):
        if type(d6_task) is not str or not d6_task:
            raise _Unknown('D6_BINDING_UNAVAILABLE')
        try:
            snapshot = self._read_d6(record, d6_task)
        except Exception:
            raise _Unknown('D6_BINDING_UNAVAILABLE') from None
        root = snapshot.get('root') if type(snapshot) is dict else None
        if type(root) is not str or not root:
            raise _Unknown('D6_BINDING_UNAVAILABLE')
        return root

    def d6_snapshot(self, record, d6_task):
        try:
            snapshot = self._read_d6(record, d6_task)
        except Exception:
            raise _Unknown('D6_BINDING_UNAVAILABLE') from None
        if type(snapshot) is not dict:
            raise _Unknown('D6_BINDING_UNAVAILABLE')
        if snapshot.get('state') == 'D6_BINDING_UNAVAILABLE':
            raise _Unknown(snapshot.get('code') or 'D6_BINDING_UNAVAILABLE')
        return snapshot

    def _read_processes(self):
        try:
            value = list(self._list_processes())
        except Exception:
            raise _Unknown('PROCESS_LIST_FAILED') from None
        for item in value:
            started = item.get('started_at') if type(item) is dict else None
            if (type(item) is not dict or type(item.get('pid')) is not int or
                    item['pid'] < 1 or
                    (item.get('cwd') is not None and type(item['cwd']) is not str) or
                    type(item.get('start_time')) is not str or not item['start_time'] or
                    not isinstance(started, datetime) or started.tzinfo is None):
                raise _Unknown('PROCESS_LIST_INVALID')
        return value

    def processes(self):
        """Read twice; only a process unchanged across both reads is stable."""
        if self._processes is None:
            before = {item['pid']: (item['start_time'], item.get('cwd'))
                      for item in self._read_processes()}
            self._processes = [
                dict(item, stable=before.get(item['pid']) ==
                     (item['start_time'], item.get('cwd')))
                for item in self._read_processes()]
        return self._processes

    def live_process(self, root, since, identity):
        """Return the pid proven to be this attempt, or None when it is proven gone.

        ``identity`` holds a trusted launch (``pid``, ``start``), a verified
        ``session``, or nothing. A process that may be the attempt but cannot be
        proven raises _Unknown; it is neither live nor dead.
        """
        if since is None:
            return None
        target = os.path.realpath(root)
        doubt = None
        for item in self.processes():
            in_root = item.get('cwd') is not None and os.path.realpath(item['cwd']) == target
            matched = _identity_match(item, identity)
            # Once a launch PID is known, no other process can be the attempt.
            if matched is False and ('pid' in identity or not in_root):
                continue
            order = _start_order(item['started_at'], since)
            if order == 'before':
                continue
            if matched is None:
                doubt = 'PROCESS_START_UNCOMPARABLE'
            elif matched is False:
                doubt = 'PROCESS_IDENTITY_UNPROVEN'
            elif not item['stable']:
                doubt = 'PROCESS_IDENTITY_UNSTABLE'
            elif order == 'overlap':
                doubt = 'PROCESS_START_AMBIGUOUS'
            elif not in_root:
                doubt = 'PROCESS_CWD_UNPROVEN'
            else:
                return item['pid']
        if doubt is not None:
            raise _Unknown(doubt)
        return None

    def claimed_session(self, task_id, worker, d6_task):
        """Return the native session of the latest CLAIMED assignment for this attempt."""
        if self._assignments is None:
            try:
                value = self.store.get_developer_assignments()
            except Exception:
                raise _Unknown('ASSIGNMENTS_UNAVAILABLE') from None
            if type(value) is not list:
                raise _Unknown('ASSIGNMENTS_UNAVAILABLE')
            self._assignments = value
        claims = [item['claim'] for item in self._assignments
                  if type(item) is dict and item.get('state') == 'CLAIMED' and
                  item.get('task_id') == task_id and item.get('worker') == worker and
                  item.get('d6_task_id') == d6_task and type(item.get('claim')) is dict]
        latest = max(claims, key=lambda claim: str(claim.get('claimed_at')), default=None)
        return latest.get('session_id') if latest is not None else None


def _executor_task(record):
    completion = record.get('completion')
    developer = record.get('developer')
    value = completion.get('executor_task_id') if type(completion) is dict else None
    if type(value) is not str and type(developer) is dict:
        value = developer.get('executor_task_id')
    return value if type(value) is str and value else None


def _dispatched_at(record):
    """Return the interval of the latest ALLOWED dispatch of the current assignment.

    Legacy entries carry no assignment_id. They stay readable as audit history,
    but nothing binds them to this attempt, so they are not launch evidence.
    """
    developer = record.get('developer')
    if type(developer) is not dict:
        return None
    worker = worker_dispatch.worker_key_for(developer.get('provider_profile'))
    assignment = developer.get('assignment_id')
    if type(assignment) is not str or not assignment:
        return None
    latest = None
    audit = record.get('dispatch_audit')
    for entry in audit if type(audit) is list else []:
        if (type(entry) is not dict or entry.get('decision') != 'ALLOWED' or
                entry.get('worker_key') != worker or
                entry.get('action') not in _DISPATCH_ACTIONS or
                entry.get('assignment_id') != assignment):
            continue
        at = _interval(entry.get('at'))
        if at is not None and (latest is None or at[0] > latest[0]):
            latest = at
    return latest


def _attempt_identity(ctx, record, d6_task):
    """Return the trusted launch or verified native session of the current attempt."""
    developer = record.get('developer')
    developer = developer if type(developer) is dict else {}
    execution = developer.get('execution')
    if type(execution) is dict and execution.get('status') == 'RUNNING':
        return {'pid': execution.get('child_pid'), 'start': execution.get('child_start_time')}
    worker = worker_dispatch.worker_key_for(developer.get('provider_profile'))
    session = ctx.claimed_session(record.get('task_id'), worker, d6_task)
    return {'session': session} if session is not None else {}


def _attempt_process(ctx, record, d6_task, binding=None):
    """Return (pid or None, dispatch interval) for a Task's current attempt.

    ``binding`` is the record that holds the D6 binding when it is not ``record``.
    """
    root = ctx.d6_root(binding or record, d6_task)
    since = _dispatched_at(record)
    return ctx.live_process(root, since, _attempt_identity(ctx, record, d6_task)), since


def _no_process(since):
    if since is None:
        return 'no ALLOWED dispatch audit for the current assignment'
    return f'no live attempt process started after {_stamp(since[0])}'


def _claims(record):
    value = record.get('review_dispatch_claim')
    if type(value) is dict:
        return [value]
    return [item for item in value if type(item) is dict] if type(value) is list else []


def _cover_record(ctx, row, record):
    """Mark what this Task owns before any read that can fail for the row."""
    ctx.covered_tasks.add(record.get('task_id'))
    number = _pr_number(row, record)
    if number is not None:
        ctx.covered_prs.add(number)
    repairs = record.get('review_repair_claims')
    for claim in repairs if type(repairs) is list else []:
        if type(claim) is dict and type(claim.get('repair_task_id')) is str:
            ctx.covered_tasks.add(claim['repair_task_id'])


def _pr_number(row, record):
    if type(row.get('pr')) is int:
        return row['pr']
    published = record.get('published_pr')
    if type(published) is dict and type(published.get('pr')) is int:
        return published['pr']
    claims = _claims(record)
    number = claims[-1].get('pr') if claims else None
    return number if type(number) is int and number > 0 else None


def _pending_checks(ctx, head):
    """Return names of exact-head checks not yet completed."""
    value = ctx.get(f'commits/{head}/check-runs?per_page=100')
    runs = value.get('check_runs') if type(value) is dict else None
    if (type(runs) is not list or type(value.get('total_count')) is not int or
            value['total_count'] != len(runs) or len(runs) > 100):
        raise _Unknown('CHECKS_UNAVAILABLE')
    pending = []
    for item in runs:
        if (type(item) is not dict or type(item.get('name')) is not str or
                type(item.get('status')) is not str):
            raise _Unknown('CHECKS_UNAVAILABLE')
        if item.get('head_sha') == head and item['status'] != 'completed':
            pending.append(re.sub(r'[^\x20-\x7e]', '?', item['name'])[:64])
    return sorted(set(pending))


def _issue_marker_status(ctx, number):
    """Return the latest waiting_owner marker status on an open issue, or None."""
    if number not in ctx._issue_markers:
        issue = ctx.get(f'issues/{number}')
        if type(issue) is not dict or issue.get('state') not in ('open', 'closed'):
            raise _Unknown('OWNER_MARKER_UNAVAILABLE')
        status = None
        if issue['state'] == 'open':
            for comment in ctx.get_pages(f'issues/{number}/comments'):
                body = comment.get('body') if type(comment) is dict else None
                if type(body) is not str:
                    raise _Unknown('OWNER_MARKER_UNAVAILABLE')
                for match in _ACTIVITY.finditer(body):
                    fields = dict(token.split('=', 1) for token in match.group(1).split()
                                  if '=' in token)
                    if fields.get('type') == 'waiting_owner':
                        status = fields.get('status')
        ctx._issue_markers[number] = status
    return ctx._issue_markers[number]


def _owner_wait(ctx, row, record):
    """Return evidence for an open waiting_owner marker, or None."""
    if record is not None:
        latest = None
        events = record.get('completion_events')
        for event in events if type(events) is list else []:
            if (type(event) is dict and event.get('source') == 'GITHUB' and
                    event.get('marker_type') == 'waiting_owner'):
                latest = event
        if latest is not None and latest.get('marker_status') == 'OPEN':
            return f"Task waiting_owner marker OPEN on #{latest.get('issue_number')}"
    ref = row.get('owner_ref')
    match = _OWNER_REF.fullmatch(ref) if type(ref) is str else None
    if match is None:
        return None
    number = int(match.group(1))
    if _issue_marker_status(ctx, number) == 'OPEN':
        return f'waiting_owner marker OPEN on open #{number}'
    return None


def _capacity(ctx):
    """HOLD when every D6 Developer route is busy or not authorized.

    One proven free route clears capacity. Without one, an uncertain route keeps
    the answer UNKNOWN, because it may be free.
    """
    free, waiting, doubts = [], [], []
    for worker in DEVELOPER_ROUTES:
        decision = ctx.evaluate(worker)
        reason = getattr(decision, 'reason_code', None)
        if getattr(decision, 'allowed', None) is not True:
            if reason in _AUTHORIZATION_WAIT_CODES:
                waiting.append(f'{worker}={reason}')
                continue
            raise _Unknown(reason if type(reason) is str else 'DISPATCH_POLICY_UNAVAILABLE')
        occupancy, detail = _route_occupancy(ctx, worker)
        if occupancy == 'FREE':
            free.append(worker)
        elif occupancy == 'BUSY':
            waiting.append(f'{worker}=busy:{detail}')
        else:
            doubts.append(detail)
    if free:
        return 'CLEARED', 'capacity cleared: free Developer routes ' + ','.join(free)
    if doubts:
        raise _Unknown(doubts[0])
    return 'HOLD', 'capacity: every Developer route busy or awaiting authorization (' + \
        ', '.join(waiting) + ')'


def _route_occupancy(ctx, worker):
    """Return ('BUSY', Task ID), ('FREE', None) or ('UNKNOWN', code) for one route.

    A Task occupies the route only under the checks that make it RUNNING, so a
    terminal or unowned Task does not count. Failed evidence is neither busy nor free.
    """
    doubt = None
    for task_id in sorted(ctx.task_ids()):
        try:
            record = ctx.record(task_id)
            developer = record.get('developer')
            if (type(developer) is not dict or
                    developer.get('state') != 'DEVELOPER_DISPATCHING' or
                    worker_dispatch.worker_key_for(developer.get('provider_profile')) != worker or
                    task_status.derive_state(record) in _TERMINAL_TASK_STATES):
                continue
            pid, _ = _attempt_process(ctx, record, _executor_task(record))
        except _Unknown as exc:
            doubt = doubt or exc.code
            continue
        if pid is not None:
            return 'BUSY', task_id
    return ('UNKNOWN', doubt) if doubt is not None else ('FREE', None)


def _merged(record):
    """True only for a recorded GitHub merge marker.

    ``task_status.derive_state`` also says COMPLETED once a Developer result is
    returned to the PM (callback or completion PM_RETURNED), before any formal
    review or merge, so that state alone never makes a row DONE.
    """
    events = record.get('completion_events')
    return task_status.derive_state(record) == 'COMPLETED' and any(
        type(event) is dict and event.get('source') == 'GITHUB' and
        event.get('marker_type') == 'merge' and event.get('marker_status') == 'MERGED'
        for event in (events if type(events) is list else []))


def _row_done(ctx, row):
    task_id = row.get('task_id')
    if type(task_id) is str:
        record = ctx.record(task_id)
        return record is not None and _merged(record)
    if type(row.get('pr')) is int:
        return ctx.pull(row['pr'])['merged']
    return False


def _gate(ctx, row, gate, rows_by_id):
    """Return (HOLD|CLEARED|ANOMALY, evidence) for one parsed predicate."""
    kind, value = gate
    if kind == 'capacity':
        return _capacity(ctx)
    if kind == 'pr_merged':
        pull = ctx.pull(value)
        if pull['merged']:
            return 'CLEARED', f'pr_merged:{value} cleared: PR merged'
        if pull['state'] == 'closed':
            return 'ANOMALY', f'pr_merged:{value} can never clear: PR closed unmerged'
        return 'HOLD', f'pr_merged:{value}: PR #{value} open'
    if kind == 'issue_closed':
        issue = ctx.get(f'issues/{value}')
        state = issue.get('state') if type(issue) is dict else None
        if state == 'closed':
            return 'CLEARED', f'issue_closed:{value} cleared'
        if state == 'open':
            return 'HOLD', f'issue_closed:{value}: issue #{value} open'
        raise _Unknown('ISSUE_UNAVAILABLE')
    if kind == 'dispatch_denied':
        task_id = row.get('task_id') if type(row.get('task_id')) is str else None
        owner_ref = row.get('owner_ref') if type(row.get('owner_ref')) is str else None
        decision = ctx.evaluate(value, owner_ref, task_id)
        reason = getattr(decision, 'reason_code', None)
        if getattr(decision, 'allowed', None) is True:
            return 'CLEARED', f'dispatch_denied:{value} cleared: {reason}'
        if reason in _AUTHORIZATION_WAIT_CODES:
            return 'HOLD', f'dispatch_denied:{value}: {reason}'
        if reason in _UNCHECKABLE_WORKER_CODES:
            return 'ANOMALY', f'dispatch_denied:{value} names no policy Worker'
        raise _Unknown(reason if type(reason) is str else 'DISPATCH_POLICY_UNAVAILABLE')
    if kind == 'lease':
        record = ctx.record(value)
        if record is None:
            return 'ANOMALY', f'lease:{value}: no such Task'
        state = task_status.derive_state(record)
        if state in ('RUNNING', 'BLOCKED'):
            return 'HOLD', f'lease:{value}: Task {state}'
        if state == 'UNKNOWN':
            raise _Unknown('LEASE_TASK_STATE_UNKNOWN')
        return 'CLEARED', f'lease:{value} cleared: Task {state}'
    if kind == 'time':
        if ctx.now < value:
            return 'HOLD', f'time:{_stamp(value)} not reached'
        return 'CLEARED', f'time:{_stamp(value)} reached'
    target = rows_by_id[value]
    if _row_done(ctx, target):
        return 'CLEARED', f'after row {_label(value)} cleared: row DONE'
    return 'HOLD', f'after row {_label(value)}: row not DONE'


def _row_gates(row, rows_by_id):
    """Parse blocker and after; return (gates, anomaly evidence or None)."""
    gates = []
    blocker = row.get('blocker')
    if blocker is not None:
        parsed = parse_predicate(blocker)
        if parsed is None:
            return None, 'blocker is free text, not a checkable predicate'
        gates.append(parsed)
    after = row.get('after')
    if after is not None:
        parsed = parse_predicate(after)
        if parsed is None and type(after) is str and after in rows_by_id:
            seen, current = {row.get('id')}, after
            while True:
                if current in seen:
                    return None, 'after forms a cycle and can never clear'
                seen.add(current)
                following = rows_by_id[current].get('after')
                if (type(following) is not str or following not in rows_by_id or
                        parse_predicate(following) is not None):
                    break
                current = following
            parsed = ('row', after)
        if parsed is None:
            return None, 'after is free text, not a predicate or row id'
        gates.append(parsed)
    return gates, None


def _finish(ctx, row, record, gates, rows_by_id, idle, *, needs_developer):
    """Apply Owner wait and predicates to work that has no live evidence."""
    wait = _owner_wait(ctx, row, record)
    if wait is not None:
        return WAITING_OWNER, [wait]
    results = [_gate(ctx, row, gate, rows_by_id) for gate in gates]
    anomalies = [evidence for status, evidence in results if status == 'ANOMALY']
    if anomalies:
        return ANOMALY, anomalies
    held = [evidence for status, evidence in results if status == 'HOLD']
    if held:
        return BLOCKED, held
    cleared = [evidence for status, evidence in results if status == 'CLEARED']
    if needs_developer and not any(gate[0] == 'capacity' for gate in gates):
        status, evidence = _capacity(ctx)
        if status == 'HOLD':
            return BLOCKED, [evidence]
        cleared.append(evidence)
    return ACTIONABLE, idle + cleared


def _classify_pr(ctx, row, number, gates, rows_by_id):
    ctx.covered_prs.add(number)
    pull = ctx.pull(number)
    head = pull['head']['sha']
    if pull['merged']:
        return DONE, [f'PR #{number} merged']
    if pull['state'] == 'closed':
        return ANOMALY, [f'PR #{number} closed unmerged but still on board']
    pending = _pending_checks(ctx, head)
    if pending:
        return WAITING_CI, [f'PR #{number} head {head[:12]} pending: ' + ','.join(pending)]
    return _finish(ctx, row, None, gates, rows_by_id,
                   [f'open PR #{number} head {head[:12]} has no Task and no pending check'],
                   needs_developer=False)


def _related(ctx, record):
    """Build decide()'s related snapshot the way task advance does, read-only."""
    events = record.get('completion_events')
    events = list(events) if type(events) is list else []
    repairs = []
    claims = record.get('review_repair_claims')
    for claim in claims if type(claims) is list else []:
        if type(claim) is not dict:
            continue
        try:
            repair_record = ctx.record(claim.get('repair_task_id'))
            state = (task_advance.derive_repair_state(repair_record)
                     if type(repair_record) is dict else 'RECORD_UNAVAILABLE')
        except _Unknown:
            state = 'RECORD_UNAVAILABLE'
        repairs.append(dict(claim, repair_state=state))
    outcomes = task_advance._review_outcomes(record, _claims(record), ctx.get)
    return {'events': events + outcomes, 'repair_claims': repairs}


def _classify_repair(ctx, row, record, head, gates, rows_by_id):
    """Classify WAITING_REPAIR from the repair Task itself, never from the parent."""
    repair = next(item for item in reversed(record['review_repair_claims'])
                  if type(item) is dict and item.get('head_sha') == head)
    task_id = repair.get('repair_task_id')
    child = ctx.record(task_id)
    derived = task_status.derive_state(child)
    if derived in _TERMINAL_TASK_STATES:
        return ANOMALY, [f'repair Task {task_id} {derived}: dead repair']
    if task_advance.derive_repair_state(child) == 'PENDING':
        return _finish(ctx, row, record, gates, rows_by_id,
                       [f'repair Task {task_id} is claimed but was never dispatched'],
                       needs_developer=True)
    executor = _executor_task(child)
    if executor is None:
        raise _Unknown('REPAIR_EXECUTOR_UNVERIFIABLE')
    if executor != repair.get('executor_task'):
        return ANOMALY, [f'repair Task {task_id} executor does not match the repair claim']
    pid, since = _attempt_process(ctx, child, executor, binding=record)
    if pid is None:
        return ANOMALY, [f'repair Task {task_id}: {_no_process(since)}: dead repair']
    return RUNNING, [f'repair Task {task_id} process {pid} proven in the D6 root, started '
                     f'after dispatch {_stamp(since[0])}']


def _classify_task(ctx, row, task_id, gates, rows_by_id):
    record = ctx.record(task_id)
    if record is None:
        return ANOMALY, [f'Task {task_id} not found in TaskStore']
    _cover_record(ctx, row, record)
    derived = task_status.derive_state(record)
    if _merged(record):
        return DONE, ['Task COMPLETED: merge marker recorded']
    if derived in ('ABANDONED', 'FAILED'):
        return ANOMALY, [f'Task {derived}: dead Task still on board']
    number = _pr_number(row, record)
    pull = ctx.pull(number) if number is not None else None
    # A terminal PR outranks any attempt, review or repair the Task still records
    # as live, so it is checked before the Developer state or any D6 or process read.
    if type(pull) is dict and pull['merged']:
        return ACTIONABLE, [f'PR #{number} merged but the Task has no merge marker: '
                            'reconcile through task advance']
    if type(pull) is dict and pull['state'] == 'closed':
        return ANOMALY, [f'PR #{number} closed unmerged but the Task is still live']
    developer = record.get('developer')
    developer_state = developer.get('state') if type(developer) is dict else None
    if developer_state == 'DEVELOPER_DISPATCHING':
        pid, since = _attempt_process(ctx, record, _executor_task(record))
        if pid is not None:
            return RUNNING, [f'process {pid} proven in the Task D6 root, started after '
                             f'dispatch {_stamp(since[0])}']
        return ANOMALY, [f'Task DEVELOPER_DISPATCHING but {_no_process(since)}: dead Task']
    if developer_state != 'DEVELOPER_RESPONDED':
        step = task_advance.decide(record, None, None, {})
        label = developer_state if type(developer_state) is str else 'none'
        return _finish(ctx, row, record, gates, rows_by_id,
                       [f'Developer state {label}; next step {step}'],
                       needs_developer=developer_state in (None, 'DEVELOPER_PENDING'))

    executor = _executor_task(record)
    d6 = ctx.d6_snapshot(record, executor) if executor is not None else None
    step = task_advance.decide(record, d6, pull, _related(ctx, record))
    head = d6.get('result_head') if type(d6) is dict else None
    idle = f'Task has next step {step} and nothing is running'
    if step == 'WAITING_REPAIR':
        return _classify_repair(ctx, row, record, head, gates, rows_by_id)
    if step == 'RECORD_REVIEW_RESULT':
        claim = max((item for item in _claims(record)
                     if item.get('pr') == number and item.get('head_sha') == head),
                    key=lambda item: item.get('version', 0))
        if claim.get('state') == 'COMPLETED':
            idle = (f"review claim v{claim.get('version')} COMPLETED: result awaiting "
                    f'ingestion, next step {step}')
        elif claim.get('state') == 'DISPATCHED' and claim.get('executor_task') == executor:
            pid = ctx.live_process(ctx.d6_root(record, claim.get('reviewer_task')),
                                   _interval(claim.get('claimed_at')),
                                   {'session': claim.get('native_session_id')})
            if pid is not None:
                return WAITING_REVIEW, [
                    f"review claim v{claim.get('version')} on PR #{number} head {head[:12]}; "
                    f'Reviewer process {pid} proven in its D6']
    if step in _CI_WAIT_STEPS and type(pull) is dict and head == pull['head']['sha']:
        pending = _pending_checks(ctx, head)
        if pending:
            return WAITING_CI, [f'PR #{number} head {head[:12]} pending: ' + ','.join(pending)]
    return _finish(ctx, row, record, gates, rows_by_id, [idle], needs_developer=False)


def _row_problem(row):
    if type(row) is not dict:
        return 'row is not an object'
    if row.get('id') is not None and (type(row['id']) is not str or not 1 <= len(row['id']) <= 128):
        return 'id is not a short string'
    if row.get('task_id') is not None and (type(row['task_id']) is not str or
                                           _TASK_ID.fullmatch(row['task_id']) is None):
        return 'task_id is not a Task ID: unknown Task'
    if row.get('pr') is not None and (type(row['pr']) is not int or
                                      not 1 <= row['pr'] <= 9999999):
        return 'pr is not a PR number'
    for field in ('owner_ref', 'after', 'blocker'):
        if row.get(field) is not None and (type(row[field]) is not str or len(row[field]) > 512):
            return f'{field} is not a short string'
    return None


def _classify_row(ctx, row, rows_by_id, duplicate_ids):
    problem = _row_problem(row)
    if problem is not None:
        return ANOMALY, [problem]
    # The row lists this PR and Task whatever its own state turns out to be, so
    # neither may later be reported as missing from the board.
    if row.get('pr') is not None:
        ctx.covered_prs.add(row['pr'])
    if row.get('task_id') is not None:
        ctx.covered_tasks.add(row['task_id'])
    if row.get('id') in duplicate_ids:
        return ANOMALY, ['duplicate row id']
    gates, problem = _row_gates(row, rows_by_id)
    if problem is not None:
        return ANOMALY, [problem]
    if row.get('task_id') is not None:
        return _classify_task(ctx, row, row['task_id'], gates, rows_by_id)
    if row.get('pr') is not None:
        return _classify_pr(ctx, row, row['pr'], gates, rows_by_id)
    return _finish(ctx, row, None, gates, rows_by_id,
                   ['row has no Task or PR: work not dispatched'], needs_developer=True)


def _unlisted(ctx):
    """Report open PRs and live Tasks that no board row covers.

    A successful list read proves the orphan, so it stays ANOMALY even when
    another row is UNKNOWN; that uncertainty stays on the affected row.
    """
    findings = []
    try:
        pulls = ctx.get_pages('pulls?state=open')
        numbers = []
        for pull in pulls:
            number = pull.get('number') if type(pull) is dict else None
            if type(number) is not int or number < 1:
                raise _Unknown('GITHUB_READ_INVALID')
            numbers.append(number)
    except _Unknown as exc:
        findings.append({'row': 'github:open-prs', 'state': UNKNOWN, 'evidence': [exc.code]})
    else:
        for number in sorted(set(numbers) - ctx.covered_prs):
            findings.append({'row': f'pr:#{number}', 'state': ANOMALY,
                             'evidence': ['open PR missing from the board']})
    try:
        task_ids = sorted(ctx.task_ids())
    except _Unknown as exc:
        return findings + [{'row': 'taskstore', 'state': UNKNOWN, 'evidence': [exc.code]}]
    for task_id in task_ids:
        if task_id in ctx.covered_tasks:
            continue
        try:
            record = ctx.record(task_id)
        except _Unknown as exc:
            findings.append({'row': f'task:{task_id}', 'state': UNKNOWN, 'evidence': [exc.code]})
            continue
        developer = record.get('developer')
        if (type(developer) is dict and developer.get('state') == 'DEVELOPER_DISPATCHING' and
                task_status.derive_state(record) not in _TERMINAL_TASK_STATES):
            findings.append({'row': f'task:{task_id}', 'state': ANOMALY,
                             'evidence': ['live Task (DEVELOPER_DISPATCHING) missing from the '
                                          'board']})
    return findings


def _label(value):
    return re.sub(r'[^\x21-\x7e]', '?', value)[:64]


def _row_label(row, index):
    if type(row) is dict and type(row.get('id')) is str and row['id']:
        return _label(row['id'])
    return f'row[{index}]'


def check_board(rows, *, store, github_get, list_processes, read_d6=None,
                evaluate=None, now=None):
    """Classify every row and return {'result', 'exit_code', 'rows'}."""
    ctx = _Context(store=store, github_get=github_get, list_processes=list_processes,
                   read_d6=read_d6 or task_advance._d6_snapshot,
                   evaluate=evaluate or worker_dispatch.evaluate,
                   now=now or datetime.now(timezone.utc))
    rows_by_id, duplicate_ids = {}, set()
    for row in rows:
        if type(row) is dict and type(row.get('id')) is str:
            if row['id'] in rows_by_id:
                duplicate_ids.add(row['id'])
            rows_by_id[row['id']] = row
    findings = []
    for index, row in enumerate(rows):
        try:
            state, evidence = _classify_row(ctx, row, rows_by_id, duplicate_ids)
        except _Unknown as exc:
            state, evidence = UNKNOWN, [exc.code]
        except Exception:
            state, evidence = UNKNOWN, ['BOARD_CHECK_INTERNAL_ERROR']
        findings.append({'row': _row_label(row, index), 'state': state, 'evidence': evidence})
    findings.extend(_unlisted(ctx))
    if any(item['state'] in FLAGGED_STATES for item in findings):
        result, code = 'FLAGGED', 2
    elif any(item['state'] == UNKNOWN for item in findings):
        result, code = 'UNKNOWN', 1
    else:
        result, code = 'CLEAN', 0
    return {'result': result, 'exit_code': code, 'rows': findings}


def github_reader(runner=None):
    """Return a read-only ``gh api`` GET adapter bound to the configured repository."""
    state = {}

    def get(path):
        if 'slug' not in state:
            from .config import repository_config
            slug = repository_config()['slug']
            if type(slug) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', slug):
                raise _Unknown('REPOSITORY_CONFIG_INVALID')
            state['slug'] = slug
        run = runner or task_advance._publish_run_gh
        result = run(os.getcwd(), ['api', '--method', 'GET', f"repos/{state['slug']}/{path}"])
        if getattr(result, 'returncode', 1) != 0 or type(getattr(result, 'stdout', None)) is not bytes:
            raise _Unknown('GITHUB_READ_FAILED')
        return json.loads(result.stdout.decode('utf-8'))
    return get


def _ps_snapshot(run, options):
    """Return {pid: (start text, start time, session IDs)}; argv is not otherwise kept."""
    # Separate -o options: in one list, some ps builds read 'pid=,...' as header text.
    result = run(['ps', '-A', '-ww', '-o', 'pid=', '-o', 'lstart=', '-o', 'command='],
                 **options)
    if result.returncode != 0:
        raise _Unknown('PROCESS_LIST_FAILED')
    rows = {}
    for line in result.stdout.decode('utf-8', 'surrogateescape').splitlines():
        parts = line.split(None, 6)
        if len(parts) < 6 or not parts[0].isdigit():
            continue
        text = ' '.join(parts[1:6])
        started = _lstart(text)
        if started is not None:
            sessions = _ARGV_SESSION.findall(parts[6]) if len(parts) > 6 else []
            rows[int(parts[0])] = (text, started.astimezone(timezone.utc), sessions)
    return rows


def list_processes(run=subprocess.run):
    """List processes with start identity and cwd (ps, lsof, ps; read-only).

    lsof and ps are separate reads, so a cwd is kept only for a PID whose start
    is the same in the ps reads on both sides of lsof. Any other PID has cwd None.
    """
    env = dict(os.environ, LC_ALL='C', LANG='C')
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                   stderr=subprocess.DEVNULL, check=False, timeout=30, env=env)
    before = _ps_snapshot(run, options)
    lsof = run(['lsof', '-w', '-n', '-P', '-a', '-u', str(os.getuid()), '-d', 'cwd',
                '-F', 'pn'], **options)
    after = _ps_snapshot(run, options)
    if lsof.returncode != 0:
        raise _Unknown('PROCESS_LIST_FAILED')
    cwd, pid = {}, None
    for line in lsof.stdout.decode('utf-8', 'surrogateescape').splitlines():
        if line.startswith('p') and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith('n') and pid is not None:
            cwd[pid] = line[1:]
    own = os.getpid()
    return [{'pid': item, 'start_time': text, 'started_at': started,
             'cwd': cwd.get(item) if before.get(item, (None,))[0] == text else None,
             'native_session_ids': sessions}
            for item, (text, started, sessions) in sorted(after.items()) if item != own]


def format_result(result):
    lines = [f'board-check: {result["result"]}']
    for item in result['rows']:
        lines.append(f"{item['row']} {item['state']} " + '; '.join(item['evidence']))
    return '\n'.join(lines)


def add_board_check_parser(sub):
    parser = sub.add_parser(
        'board-check',
        help='Reconcile pm-board.json rows with Task, GitHub and Worker evidence; read-only')
    parser.add_argument('--board', help='pm-board.json path '
                        '(default: ~/.local/state/llmtool/pm-board.json)')
    parser.add_argument('--json', action='store_true', help='output JSON')
    return parser


def run_board_check(args, *, store=None, github_get=None, list_processes_call=None,
                    read_d6=None, evaluate=None, now=None):
    path = getattr(args, 'board', None) or default_board_path()
    as_json = bool(getattr(args, 'json', False))
    try:
        rows = read_board(path)
    except BoardUnreadable:
        if as_json:
            print(json.dumps({'result': 'UNREADABLE', 'exit_code': 1, 'rows': []},
                             ensure_ascii=True, sort_keys=True))
        else:
            print('board-check: UNREADABLE board file')
        return 1
    result = check_board(rows, store=store or TaskStore(),
                         github_get=github_get or github_reader(),
                         list_processes=list_processes_call or list_processes,
                         read_d6=read_d6, evaluate=evaluate, now=now)
    if as_json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    else:
        print(format_result(result))
    return result['exit_code']
