"""Board reading, time/predicate helpers, runtime adapters and CLI for ``llmtool board-check`` (#763)."""

import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import task_advance, worker_dispatch
from .task_store import TaskStore


def _bc():
    name = f'{__package__}.board_check' if __package__ else 'board_check'
    mod = sys.modules.get(name)
    if mod is None:
        from . import board_check as mod
    return mod


def default_board_path():
    return Path.home() / '.local' / 'state' / 'llmtool' / 'pm-board.json'


def _reject_constant(_):
    raise ValueError('non-finite number')


def read_board(path):
    """Return the board rows; any doubt about the file raises BoardUnreadable."""
    bc = _bc()
    try:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > bc.MAX_BOARD_BYTES:
                raise bc.BoardUnreadable('board is not a bounded regular file')
            chunks, remaining = [], bc.MAX_BOARD_BYTES + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(fd)
        raw = b''.join(chunks)
        if len(raw) > bc.MAX_BOARD_BYTES:
            raise bc.BoardUnreadable('board exceeds size limit')
        value = json.loads(raw.decode('utf-8'),
                           object_pairs_hook=worker_dispatch._unique_pairs,
                           parse_constant=bc._reject_constant)
    except bc.BoardUnreadable:
        raise
    except (OSError, UnicodeError, ValueError, TypeError):
        raise bc.BoardUnreadable('board unreadable') from None
    if (type(value) is not dict or type(value.get('schema_version')) is not int or
            value['schema_version'] != bc.BOARD_SCHEMA_VERSION or
            type(value.get('rows')) is not list):
        raise bc.BoardUnreadable('board is not schema_version 1 with a rows list')
    return value['rows']


def _utc(value):
    bc = _bc()
    if type(value) is not str or bc._UTC.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def _interval(value):
    """Return the [start, end) a recorded UTC stamp covers at its own precision."""
    bc = _bc()
    parsed = bc._utc(value)
    if parsed is None:
        return None
    digits = len(re.sub(r'(?:Z|\+00:00)\Z', '', value).partition('.')[2])
    return parsed, parsed + timedelta(seconds=10 ** -digits)


def _start_order(started_at, since):
    """Place a process start against an event: 'before', 'after' or 'overlap'.

    Equal whole seconds overlap, so they never establish an order.
    """
    bc = _bc()
    if started_at + bc._PROCESS_START_RESOLUTION <= since[0]:
        return 'before'
    return 'after' if started_at >= since[1] else 'overlap'


def _lstart(text):
    bc = _bc()
    try:
        return datetime.strptime(text, bc._LSTART_FORMAT)
    except ValueError:
        return None


def _identity_match(item, identity):
    """True or False for one process; None when its start cannot be compared."""
    bc = _bc()
    if 'pid' in identity:
        if item['pid'] != identity['pid']:
            return False
        recorded = ' '.join(identity['start'].split())
        if item['start_time'] == recorded:
            return True
        # A comparable different start means the PID was reused.
        return False if bc._lstart(recorded) is not None else None
    return identity.get('session') in item.get('native_session_ids', ())


def _stamp(value):
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_predicate(text):
    """Parse one checkable predicate; free text returns None."""
    bc = _bc()
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
    if kind == 'lease' and bc._TASK_ID.fullmatch(value):
        return (kind, value)
    if kind == 'time' and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value):
        parsed = bc._utc(value)
        return (kind, parsed) if parsed is not None else None
    return None


def github_reader(runner=None):
    """Return a read-only ``gh api`` GET adapter bound to the configured repository."""
    bc = _bc()
    state = {}

    def get(path):
        if 'slug' not in state:
            from .config import repository_config
            slug = repository_config()['slug']
            if type(slug) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', slug):
                raise bc._Unknown('REPOSITORY_CONFIG_INVALID')
            state['slug'] = slug
        run = runner or task_advance._publish_run_gh
        result = run(os.getcwd(), ['api', '--method', 'GET', f"repos/{state['slug']}/{path}"])
        if getattr(result, 'returncode', 1) != 0 or type(getattr(result, 'stdout', None)) is not bytes:
            raise bc._Unknown('GITHUB_READ_FAILED')
        return json.loads(result.stdout.decode('utf-8'))
    return get


def _ps_snapshot(run, options):
    """Return {pid: (start text, start time, session IDs)}; argv is not otherwise kept."""
    bc = _bc()
    # Separate -o options: in one list, some ps builds read 'pid=,...' as header text.
    result = run(['ps', '-A', '-ww', '-o', 'pid=', '-o', 'lstart=', '-o', 'command='],
                 **options)
    if result.returncode != 0:
        raise bc._Unknown('PROCESS_LIST_FAILED')
    rows = {}
    for line in result.stdout.decode('utf-8', 'surrogateescape').splitlines():
        parts = line.split(None, 6)
        if len(parts) < 6 or not parts[0].isdigit():
            continue
        text = ' '.join(parts[1:6])
        started = bc._lstart(text)
        if started is not None:
            sessions = bc._ARGV_SESSION.findall(parts[6]) if len(parts) > 6 else []
            rows[int(parts[0])] = (text, started.astimezone(timezone.utc), sessions)
    return rows


def list_processes(run=subprocess.run):
    """List processes with start identity and cwd (ps, lsof, ps; read-only).

    lsof and ps are separate reads, so a cwd is kept only for a PID whose start
    is the same in the ps reads on both sides of lsof. Any other PID has cwd None.
    """
    bc = _bc()
    env = dict(os.environ, LC_ALL='C', LANG='C')
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                   stderr=subprocess.DEVNULL, check=False, timeout=30, env=env)
    before = bc._ps_snapshot(run, options)
    lsof = run(['lsof', '-w', '-n', '-P', '-a', '-u', str(os.getuid()), '-d', 'cwd',
                '-F', 'pn'], **options)
    after = bc._ps_snapshot(run, options)
    if lsof.returncode != 0:
        raise bc._Unknown('PROCESS_LIST_FAILED')
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
    parser.add_argument('--claim', help='claim or bind a pm-board.json row')
    parser.add_argument('--holder', help='holder name for row claim')
    parser.add_argument('--task', help='task ID to bind to a claimed row')
    return parser


def run_board_check(args, *, store=None, github_get=None, list_processes_call=None,
                    read_d6=None, evaluate=None, now=None, ledger=None):
    bc = _bc()
    claim_row_id = getattr(args, 'claim', None)
    if claim_row_id is not None:
        holder = getattr(args, 'holder', None)
        if not holder:
            print('board-check: --claim requires --holder', file=sys.stderr)
            return 2
        path = getattr(args, 'board', None) or bc.default_board_path()
        try:
            rows = bc.read_board(path)
        except bc.BoardUnreadable:
            print('board-check: UNREADABLE board file')
            return 1
        active_ledger = ledger if ledger is not None else bc.BoardLedger()
        active_store = store if store is not None else TaskStore()
        task_id = getattr(args, 'task', None)
        try:
            bc.claim_row(active_ledger, rows, claim_row_id, holder,
                         task_id=task_id, store=active_store, now=now)
        except bc.ClaimRefused as exc:
            print(exc.code)
            return bc.CLAIM_REFUSED_EXIT
        if task_id:
            print(f'claimed {claim_row_id} for {holder} bound to {task_id}')
        else:
            print(f'claimed {claim_row_id} for {holder}')
        return 0

    path = getattr(args, 'board', None) or bc.default_board_path()
    as_json = bool(getattr(args, 'json', False))
    try:
        rows = bc.read_board(path)
    except bc.BoardUnreadable:
        if as_json:
            print(json.dumps({'result': 'UNREADABLE', 'exit_code': 1, 'rows': []},
                             ensure_ascii=True, sort_keys=True))
        else:
            print('board-check: UNREADABLE board file')
        return 1

    active_ledger = ledger if ledger is not None else bc.BoardLedger()
    ledger_unreadable = False
    try:
        claims = active_ledger.read()
    except bc.LedgerUnreadable:
        claims = None
        ledger_unreadable = True

    result = bc.check_board(rows, store=store or TaskStore(),
                            github_get=github_get or bc.github_reader(),
                            list_processes=list_processes_call or bc.list_processes,
                            read_d6=read_d6, evaluate=evaluate, now=now,
                            ledger_claims=claims,
                            ledger_unreadable=ledger_unreadable)
    if as_json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    else:
        print(bc.format_result(result))
    return result['exit_code']
