"""Issue #135 task-scoped Codex tool-event audit collector (NOT an activation).

The collector is cooperative and observe-only. A Codex ``PreToolUse`` /
``PostToolUse`` command hook pipes each event to ``record`` and the collector
appends it to a private local store: a 0700 directory with 0600 files, kept
outside every repository and the Git common-dir. ``summarize`` then builds a
deterministic, allowlisted per-task summary that is safe to commit. It never
contains raw argv, environment, paths, tokens or stdout. Each action is cited
by a keyed HMAC reference that only a holder of the local key can recompute.

Truthfulness rules:
- ``WOULD_PROMPT_UNDER_OLD_PROFILE`` is an inference from a reviewed static
  mapping of the prior workspace-write + on-request profile. It is never a
  genuine owner approval.
- ``BLOCKED`` requires a collector deny record, natively verified deny
  enforcement (``AuditPolicy.deny_verified``) and no PostToolUse. Anything
  else observed after the fact is ``DETECTED_AFTER_EXECUTION``, or
  ``UNKNOWN`` when there is no post event.
- Hook coverage, trust and fail-open behaviour of the installed Codex are
  NOT_VERIFIED until a reviewed native smoke. Until
  ``AuditPolicy.coverage_verified`` is set, the summary status is
  ``AUDIT_INCOMPLETE``. A full-access Codex process can bypass hooks entirely,
  and same-UID code can tamper with the store. Neither is prevented here.

Importing this module does nothing. ``python -I -B -m llmtool.codex_approval_audit``
exposes ``init``, ``record`` and ``summarize``; it installs no hooks and
changes no Codex configuration.
"""

import fcntl
import hashlib
import hmac
import json
import os
import posixpath
import re
import shlex
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field

from .errors import LLMToolError

SCHEMA_VERSION = 1
MAX_EVENT_BYTES = 1024 * 1024
RESPONSE_CAP_BYTES = 64 * 1024
GIT_TIMEOUT_SECONDS = 10
KEY_BYTES = 32
REF_HEX = 32
EVENTS, KEY, LOCK, SEQ = 'events.jsonl', 'key', 'lock', 'seq'
TOOL_EVENTS = ('PreToolUse', 'PostToolUse')
END_EVENTS = ('Stop', 'SessionEnd', 'Interrupt')
# Required fields measured from the hook JSON schemas embedded in codex-cli 0.162.1.
REQUIRED = {
    'PreToolUse': ('cwd', 'hook_event_name', 'model', 'permission_mode', 'session_id', 'tool_input',
                   'tool_name', 'tool_use_id', 'transcript_path', 'turn_id'),
    'PostToolUse': ('cwd', 'hook_event_name', 'model', 'permission_mode', 'session_id', 'tool_input',
                    'tool_name', 'tool_response', 'tool_use_id', 'transcript_path', 'turn_id'),
}
_ID = re.compile(r'[A-Za-z0-9._:-]{1,80}')
_TASK = re.compile(r'[a-z0-9][a-z0-9._-]{0,79}')
_SHA = re.compile(r'[0-9a-f]{40}')

# Tool names are UNVERIFIED until a native smoke; anything else is UNKNOWN.
SHELL_TOOLS = frozenset({'Bash', 'shell', 'local_shell', 'exec_command', 'unified_exec'})
PATCH_TOOLS = frozenset({'apply_patch'})
MCP_PREFIX = 'mcp__'

READ_PROGRAMS = frozenset({
    'cat', 'cut', 'diff', 'echo', 'false', 'file', 'find', 'grep', 'head', 'less', 'ls', 'nl', 'pwd',
    'realpath', 'rg', 'sed', 'sha256sum', 'shasum', 'sort', 'stat', 'tail', 'test', 'tr', 'true', 'uniq',
    'wc', 'which'})
WRITE_PROGRAMS = frozenset({'chmod', 'cp', 'ln', 'mkdir', 'mv', 'rm', 'rmdir', 'tee', 'touch'})
BUILD_PROGRAMS = frozenset({'make', 'pytest', 'python', 'python3'})
NETWORK_PROGRAMS = frozenset({'curl', 'ftp', 'nc', 'rsync', 'scp', 'sftp', 'ssh', 'telnet', 'wget'})
SYSTEM_PROGRAMS = frozenset({'csrutil', 'defaults', 'diskutil', 'doas', 'kill', 'killall', 'launchctl',
                             'pkill', 'spctl', 'su', 'sudo', 'systemsetup'})
INSTALL_PROGRAMS = frozenset({'brew', 'npm', 'npx', 'pip', 'pip3', 'pipx', 'port', 'uv'})
SECRET_PROGRAMS = frozenset({'env', 'printenv', 'security'})
PROVIDER_PROGRAMS = frozenset({'claude', 'codex', 'llmtool', 'openai'})
SHELLS = frozenset({'bash', 'node', 'perl', 'ruby', 'sh', 'zsh'})
WRAPPERS = frozenset({'command', 'exec', 'nice', 'nohup', 'time', 'timeout'})
GIT_READ = frozenset({'blame', 'cat-file', 'diff', 'grep', 'log', 'ls-files', 'ls-tree', 'rev-parse',
                      'show', 'status'})
GIT_WRITE = frozenset({'add', 'commit', 'restore', 'switch', 'mv', 'rm'})
GIT_NETWORK = frozenset({'fetch', 'ls-remote', 'push'})
GIT_NOGO = frozenset({'checkout', 'clean', 'filter-branch', 'gc', 'merge', 'prune', 'pull', 'rebase',
                      'reflog', 'replace', 'reset', 'stash', 'tag', 'update-ref', 'worktree'})
KNOWN_PROGRAMS = (READ_PROGRAMS | WRITE_PROGRAMS | BUILD_PROGRAMS | NETWORK_PROGRAMS | SYSTEM_PROGRAMS |
                  INSTALL_PROGRAMS | SECRET_PROGRAMS | PROVIDER_PROGRAMS | SHELLS | {'gh', 'git'})
KNOWN_GIT = GIT_READ | GIT_WRITE | GIT_NETWORK | GIT_NOGO | {'branch', 'remote'}
_SHELL_META = re.compile(r'[;&|<>`$(){}\n]')
_SECRET_HINT = re.compile(r'(?i)(auth\.json|(^|/)\.ssh(/|$)|\.netrc|(^|/)\.aws(/|$)|keychain|'
                          r'(token|secret|passw(or)?d|api[_-]?key|credential)[A-Za-z_]*=)')
_GIT_VALUE_OPTIONS = ('-C', '-c', '--git-dir', '--work-tree', '--namespace')

RULES = {
    'R00': 'tool name not in the reviewed mapping',
    'R01': 'read-only program inside the task root',
    'R02': 'workspace write/build inside the task root (no prompt under the old sandbox)',
    'R03': 'git read-only subcommand',
    'R10': 'git write to the shared common-dir outside the old writable root',
    'R11': 'git/gh network operation (old profile had no network)',
    'R12': 'write to a path outside the task root but not a protected root',
    'R20': 'destructive or unknown git operation',
    'R21': 'sudo/system modification',
    'R22': 'network tool (egress)',
    'R23': 'package installation',
    'R24': 'secret or credential access',
    'R25': 'provider/model CLI (provider egress)',
    'R26': 'path inside a protected root (peer, canonical, common-dir, Codex home)',
    'R27': 'working directory outside the task root',
    'R28': 'recursive delete needs review',
    'R29': 'touches a pinned config/collector path',
    'R30': 'compound shell or substitution cannot be statically classified',
    'R31': 'inline interpreter code cannot be statically classified',
    'R32': 'unrecognised program',
    'R40': 'patch inside the task path allowlist',
    'R41': 'patch outside the task path allowlist',
    'R50': 'MCP tool: old-profile approval behaviour unknown',
    'R51': 'unparseable tool input',
}
ROUTINE = 'ROUTINE_NO_PROMPT_INFERRED'
WOULD_PROMPT = 'WOULD_PROMPT_UNDER_OLD_PROFILE'
UNAUTHORIZED = 'UNAUTHORIZED'
SEPARATE = 'SEPARATE_APPROVAL_REQUIRED'
UNKNOWN = 'UNKNOWN'
_RANK = {ROUTINE: 0, WOULD_PROMPT: 1, UNKNOWN: 2, SEPARATE: 3, UNAUTHORIZED: 4}


class AuditError(LLMToolError):
    """Fixed-text audit failure; never carries event content."""


@dataclass(frozen=True)
class AuditPolicy:
    codex_root: str
    home: str
    protected_roots: tuple
    allowed_paths: tuple = ()
    pinned_paths: tuple = ()
    deny_verified: bool = False
    coverage_verified: bool = False


# ------------------------------------------------------------------- store
def _under(path, root):
    return path == root or path.startswith(root.rstrip('/') + '/')


def _check_private(info, directory):
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077
            or (not directory and info.st_nlink != 1)):
        raise AuditError('audit store is not private')


def _open_store(store, forbidden=(), create=False):
    if not os.path.isabs(store) or os.path.normpath(store) != store:
        raise AuditError('audit store path must be absolute and normalized')
    real = os.path.realpath(store)
    if real != store or any(_under(store, os.path.normpath(r)) or _under(real, os.path.realpath(r))
                            for r in forbidden):
        raise AuditError('audit store must be outside repositories and not behind a symlink')
    if create:
        os.mkdir(store, 0o700)
    fd = os.open(store, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _check_private(os.fstat(fd), True)
    except Exception:
        os.close(fd)
        raise
    return fd


def _open_file(dir_fd, name, flags, create=False):
    flags |= os.O_NOFOLLOW | (os.O_CREAT | os.O_EXCL if create else 0)
    fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
    try:
        _check_private(os.fstat(fd), False)
    except Exception:
        os.close(fd)
        raise
    return fd


def _read_all(fd):
    chunks = []
    while True:
        chunk = os.read(fd, 1 << 20)
        if not chunk:
            return b''.join(chunks)
        chunks.append(chunk)


def init_store(store, forbidden=()):
    """Create a new private store with a fresh random HMAC key (exclusive)."""
    dir_fd = _open_store(store, forbidden, create=True)
    try:
        fd = _open_file(dir_fd, KEY, os.O_WRONLY, create=True)
        try:
            os.write(fd, os.urandom(KEY_BYTES))
            os.fsync(fd)
        finally:
            os.close(fd)
        for name in (LOCK, SEQ, EVENTS):
            os.close(_open_file(dir_fd, name, os.O_WRONLY, create=True))
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _key(dir_fd):
    fd = _open_file(dir_fd, KEY, os.O_RDONLY)
    try:
        key = _read_all(fd)
    finally:
        os.close(fd)
    if len(key) != KEY_BYTES:
        raise AuditError('audit key is invalid')
    return key


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def _mac(key, label, data):
    return hmac.new(key, label + b'\0' + data, hashlib.sha256).hexdigest()


def _cap_response(response):
    """Bounded private copy of tool_response; the exit status is kept even when truncated."""
    exit_code = None
    if isinstance(response, dict):
        for name in ('exit_code', 'exitCode'):
            if isinstance(response.get(name), int) and not isinstance(response.get(name), bool):
                exit_code = response[name]
                break
    raw = _canonical(response)
    if len(raw) <= RESPONSE_CAP_BYTES:
        return response, exit_code, False
    return None, exit_code, True


def _parse_event(raw):
    if len(raw) > MAX_EVENT_BYTES:
        return None, 'OVERSIZE'
    try:
        event = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None, 'NOT_UTF8_JSON'
    if not isinstance(event, dict) or not isinstance(event.get('hook_event_name'), str):
        return None, 'NOT_HOOK_EVENT'
    name = event['hook_event_name']
    for key in REQUIRED.get(name, ('session_id',)):
        if key not in event:
            return None, 'MISSING_FIELD'
    for key in ('session_id', 'turn_id', 'tool_use_id'):
        if key in event and not (isinstance(event[key], str) and _ID.fullmatch(event[key])):
            return None, 'INVALID_ID'
    if name in TOOL_EVENTS and not (isinstance(event['tool_name'], str) and isinstance(event['cwd'], str)):
        return None, 'INVALID_FIELD'
    return event, None


def record_event(store, raw, decision=None, forbidden=(), now_ns=None):
    """Append one hook event. Malformed input is recorded by size/reason only."""
    received = time.time_ns() if now_ns is None else now_ns
    event, problem = _parse_event(raw)
    if problem:
        body = {'v': SCHEMA_VERSION, 'kind': 'malformed', 'reason': problem, 'size': len(raw),
                'received_ns': received}
    else:
        name = event['hook_event_name']
        body = {'v': SCHEMA_VERSION, 'kind': 'hook', 'event': name, 'received_ns': received,
                'session_id': event['session_id']}
        for key in ('turn_id', 'tool_use_id', 'tool_name', 'cwd', 'tool_input'):
            if key in event:
                body[key] = event[key]
        if name == 'PostToolUse':
            body['tool_response'], body['exit_code'], body['truncated'] = _cap_response(event['tool_response'])
        if decision is not None:
            if decision not in ('allow', 'deny') or name != 'PreToolUse':
                raise AuditError('invalid collector decision')
            body['decision'] = decision
    dir_fd = _open_store(store, forbidden)
    try:
        key = _key(dir_fd)
        lock_fd = _open_file(dir_fd, LOCK, os.O_RDWR)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            seq_fd = _open_file(dir_fd, SEQ, os.O_RDWR)
            try:
                events_fd = _open_file(dir_fd, EVENTS, os.O_WRONLY | os.O_APPEND)
            except Exception:
                os.close(seq_fd)
                raise
            try:
                text = _read_all(seq_fd).strip()
                body['seq'] = (int(text) if text else 0) + 1
                line = dict(body, mac=_mac(key, b'record', _canonical(body)))
                data = _canonical(line) + b'\n'
                if os.write(events_fd, data) != len(data):
                    raise AuditError('audit append was short')
                os.fsync(events_fd)
                os.lseek(seq_fd, 0, os.SEEK_SET)
                os.ftruncate(seq_fd, 0)
                os.write(seq_fd, str(body['seq']).encode())
                os.fsync(seq_fd)
            finally:
                os.close(events_fd)
                os.close(seq_fd)
        finally:
            os.close(lock_fd)  # Closing releases the flock.
    finally:
        os.close(dir_fd)
    return body['seq']


def read_store(store, forbidden=()):
    """Return (key, records, gaps, store_mac). Integrity problems become gaps, never silent."""
    dir_fd = _open_store(store, forbidden)
    try:
        key = _key(dir_fd)
        fd = _open_file(dir_fd, EVENTS, os.O_RDONLY)
        try:
            raw = _read_all(fd)
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)
    records, gaps = [], []
    lines = raw.split(b'\n')
    if lines and lines[-1]:
        gaps.append({'gap': 'TORN_RECORD'})
    expected = 1
    for data in lines[:-1]:
        try:
            line = json.loads(data.decode('utf-8'))
            mac = line.pop('mac')
        except (UnicodeDecodeError, ValueError, KeyError, AttributeError, TypeError):
            gaps.append({'gap': 'UNREADABLE_RECORD'})
            continue
        if not hmac.compare_digest(str(mac), _mac(key, b'record', _canonical(line))):
            gaps.append({'gap': 'INTEGRITY_MISMATCH'})
            continue
        if line.get('seq') != expected:
            gaps.append({'gap': 'SEQUENCE_ANOMALY', 'seq': line.get('seq')})
        expected = (line.get('seq') if isinstance(line.get('seq'), int) else expected) + 1
        line['ref'] = _mac(key, b'ref', data)[:REF_HEX]
        records.append(line)
        if line.get('kind') == 'malformed':
            gaps.append({'gap': 'MALFORMED_EVENT', 'reason': line.get('reason'), 'seq': line.get('seq')})
    return key, records, gaps, _mac(key, b'store', raw)


# ---------------------------------------------------------- classification
def _norm(path, cwd):
    if not isinstance(path, str) or '\0' in path:
        return None
    return posixpath.normpath(path if path.startswith('/') else posixpath.join(cwd, path))


def _place(path, policy):
    """Role-specific placeholder for a path; never the path itself."""
    if path is None:
        return '<INVALID_PATH>'
    if _under(path, policy.codex_root):
        return '<CODEX_ROOT>' if path == policy.codex_root else '<CODEX_ROOT>/...'
    if any(_under(path, r) for r in policy.protected_roots):
        return '<PROTECTED_ROOT>'
    if _under(path, policy.home):
        return '<HOME>/...'
    return '<OUTSIDE>'


def _verdict(results):
    return max(results, key=lambda item: _RANK[item[0]])


def _path_rules(paths, writes, policy):
    results = []
    for path in paths:
        if any(_under(path, p) for p in policy.pinned_paths):
            results.append((SEPARATE, 'R29'))
        elif any(_under(path, r) for r in policy.protected_roots):
            results.append((UNAUTHORIZED, 'R26'))
        elif writes and not _under(path, policy.codex_root):
            results.append((WOULD_PROMPT, 'R12'))
    return results


def _argv(tool_input):
    command = tool_input.get('command') if isinstance(tool_input, dict) else tool_input
    if isinstance(command, list) and command and all(isinstance(a, str) for a in command):
        if len(command) >= 3 and posixpath.basename(command[0]) in SHELLS and command[1] in ('-c', '-lc'):
            return command[2], None
        return None, list(command)
    if isinstance(command, str):
        return command, None
    return None, None


def _classify_argv(argv, cwd, policy):
    while argv and (posixpath.basename(argv[0]) in WRAPPERS or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', argv[0])):
        argv = argv[1:]
        if argv and argv[0].startswith('-'):
            argv = argv[1:]
    if not argv:
        return (UNKNOWN, 'R51'), '<EMPTY>', []
    program = posixpath.basename(argv[0])
    args = argv[1:]
    flags = []
    candidates = args + [a.split('=', 1)[1] for a in args if '=' in a]
    paths = [_norm(a, cwd) for a in candidates if a.startswith(('/', '.')) or ('/' in a and '=' not in a)]
    paths = [p for p in paths if p is not None]
    if any(a.startswith('~') for a in args):
        paths.append(policy.home)
    if any(_SECRET_HINT.search(a) for a in args):
        return (UNAUTHORIZED, 'R24'), program if program in KNOWN_PROGRAMS else '<OTHER_PROGRAM>', ['secret-hint']
    if program == 'git':
        sub, skip = '', False
        for a in args:
            if skip:
                skip = False
            elif a in _GIT_VALUE_OPTIONS:
                skip = True
            elif not a.startswith('-'):
                sub = a
                break
        if any(a in ('-C', '--git-dir', '--work-tree') or a.startswith(('--git-dir=', '--work-tree='))
               for a in args):
            flags.append('git-dir-override')
        if '-c' in args[:args.index(sub)] if sub else '-c' in args:
            flags.append('config-override')
        synopsis = 'git ' + (sub if sub in KNOWN_GIT else '<OTHER>')
        force = any(a in ('-f', '--force', '--force-with-lease', '-D', '--hard', '--delete') or a.startswith('+')
                    for a in args)
        if force:
            flags.append('force')
        rest = args[args.index(sub):] if sub else []
        if sub in GIT_READ or (sub == 'branch' and all(a in ('branch', '--show-current', '-a', '-r', '--list', '-v')
                                                      for a in rest)) or (sub == 'remote' and rest == ['remote', '-v']):
            result = (ROUTINE, 'R03')
        elif (force or sub in GIT_NOGO or sub not in KNOWN_GIT or 'config-override' in flags
              or (sub == 'switch' and 'main' in rest)):
            result = (UNAUTHORIZED, 'R20')
        elif sub in GIT_NETWORK:
            result = (WOULD_PROMPT, 'R11')
        else:
            result = (WOULD_PROMPT, 'R10')
        return _verdict([result] + _path_rules(paths, result[0] != ROUTINE, policy)), synopsis, flags
    if program in SYSTEM_PROGRAMS:
        return (UNAUTHORIZED, 'R21'), program, flags
    if program in NETWORK_PROGRAMS:
        return (UNAUTHORIZED, 'R22'), program, flags
    if program in INSTALL_PROGRAMS:
        return (UNAUTHORIZED, 'R23'), program, flags
    if program in SECRET_PROGRAMS:
        return (UNAUTHORIZED, 'R24'), program, flags
    if program in PROVIDER_PROGRAMS:
        return (UNAUTHORIZED, 'R25'), program, flags
    if program == 'gh':
        return _verdict([(WOULD_PROMPT, 'R11')] + _path_rules(paths, False, policy)), 'gh', flags
    if program in SHELLS or (program in ('python', 'python3') and any(a in ('-c', '-e') for a in args)):
        return (UNKNOWN, 'R31'), program, ['inline-code']
    if program in READ_PROGRAMS:
        writes = program == 'sed' and any(a.startswith('-i') for a in args) or \
            program == 'find' and any(a in ('-delete', '-exec', '-execdir') for a in args)
        base = (UNKNOWN, 'R31') if writes and program == 'find' else (ROUTINE, 'R02' if writes else 'R01')
        return _verdict([base] + _path_rules(paths, writes, policy)), program, flags
    if program in WRITE_PROGRAMS or program in BUILD_PROGRAMS:
        base = (ROUTINE, 'R02')
        if program == 'rm' and any(re.fullmatch(r'-[A-Za-z]*[rR][A-Za-z]*|--recursive', a) for a in args):
            base = (UNKNOWN, 'R28')
            flags.append('recursive')
        return _verdict([base] + _path_rules(paths, program in WRITE_PROGRAMS, policy)), program, flags
    return (UNKNOWN, 'R32'), '<OTHER_PROGRAM>', flags


def _scan_tokens(text):
    """Best-effort no-go scan of a compound command; never upgrades to ROUTINE."""
    words = set(re.findall(r'[A-Za-z0-9_.+-]+', text))
    for programs, rule in ((SYSTEM_PROGRAMS, 'R21'), (NETWORK_PROGRAMS, 'R22'), (INSTALL_PROGRAMS, 'R23'),
                           (SECRET_PROGRAMS, 'R24'), (PROVIDER_PROGRAMS, 'R25')):
        if words & programs:
            return UNAUTHORIZED, rule
    if _SECRET_HINT.search(text):
        return UNAUTHORIZED, 'R24'
    if 'git' in words and words & (GIT_NOGO | {'--force', '-f', '--hard', '-D'}):
        return UNAUTHORIZED, 'R20'
    return UNKNOWN, 'R30'


def _patch_paths(tool_input):
    text = tool_input.get('input', tool_input.get('patch')) if isinstance(tool_input, dict) else tool_input
    if not isinstance(text, str):
        return None
    return re.findall(r'^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$', text, re.M)


def classify(tool_name, tool_input, cwd, policy):
    """Return (kind, classification, rule_id, synopsis, flags). Pure and deterministic."""
    cwd_path = _norm(cwd, '/')
    if cwd_path is None or not cwd_path.startswith('/'):
        return 'unknown', UNKNOWN, 'R51', '<INVALID_CWD>', []
    outside = not _under(cwd_path, policy.codex_root)
    if tool_name in SHELL_TOOLS:
        kind = 'shell'
        text, argv = _argv(tool_input)
        if text is not None and _SHELL_META.search(text):
            result, synopsis, flags = _scan_tokens(text), '<COMPOUND_SHELL>', ['compound']
        else:
            if text is not None:
                try:
                    argv = shlex.split(text)
                except ValueError:
                    argv = None
            if argv:
                result, synopsis, flags = _classify_argv(argv, cwd_path, policy)
            else:
                result, synopsis, flags = (UNKNOWN, 'R51'), '<UNPARSEABLE>', []
    elif tool_name in PATCH_TOOLS:
        kind, synopsis, flags = 'patch', 'apply_patch', []
        found = _patch_paths(tool_input)
        if not found:
            result = (UNKNOWN, 'R51')
        else:
            paths = [_norm(a or b, cwd_path) for a, b in found]
            if None in paths:
                result = (UNKNOWN, 'R51')
            else:
                allowed = all(any(_under(p, posixpath.join(policy.codex_root, a)) for a in policy.allowed_paths)
                              for p in paths)
                result = _verdict([(ROUTINE, 'R40') if allowed else (UNAUTHORIZED, 'R41')] +
                                  _path_rules(paths, True, policy))
    elif isinstance(tool_name, str) and tool_name.startswith(MCP_PREFIX):
        kind, result, synopsis, flags = 'mcp', (UNKNOWN, 'R50'), 'mcp', []
    else:
        kind, result, synopsis, flags = 'unknown', (UNKNOWN, 'R00'), '<UNKNOWN_TOOL>', []
    if outside:
        result = _verdict([result, (UNAUTHORIZED, 'R27')])
    return kind, result[0], result[1], synopsis, sorted(set(flags))


# ------------------------------------------------------------- correlation
def correlate(records):
    """Match Pre/Post by (session_id, turn_id, tool_use_id) in store order."""
    actions, order, gaps, ended = {}, [], [], {}
    for rec in records:
        if rec.get('kind') != 'hook':
            continue
        if rec['event'] in END_EVENTS:
            ended.setdefault(rec['session_id'], rec['seq'])
            continue
        if rec['event'] not in TOOL_EVENTS:
            continue
        key = (rec['session_id'], rec['turn_id'], rec['tool_use_id'])
        if key not in actions:
            actions[key] = {'pre': [], 'post': []}
            order.append(key)
        actions[key]['pre' if rec['event'] == 'PreToolUse' else 'post'].append(rec)
    result = []
    for key in order:
        pre, post = actions[key]['pre'], actions[key]['post']
        problems = []
        if len(pre) > 1:
            problems.append('DUPLICATE_PRE')
        if len(post) > 1:
            problems.append('DUPLICATE_POST')
        if not pre:
            problems.append('MISSING_PRE')
        if not post:
            end = ended.get(key[0])
            problems.append('INTERRUPTED_NO_POST' if end and end > pre[0]['seq'] else 'MISSING_POST')
        if pre and post and post[0]['seq'] < pre[0]['seq']:
            problems.append('POST_BEFORE_PRE')
        if pre and post and (pre[0]['tool_name'] != post[0]['tool_name'] or
                             _canonical(pre[0]['tool_input']) != _canonical(post[0]['tool_input']) or
                             pre[0]['cwd'] != post[0]['cwd']):
            problems.append('PRE_POST_MISMATCH')
        result.append({'key': key, 'pre': pre[:1], 'post': post[:1], 'problems': problems})
        gaps.extend({'gap': p, 'action': len(result)} for p in problems)
    return result, gaps


def _enforcement(classification, pre, post, policy):
    if classification == ROUTINE:
        return 'NOT_REQUIRED'
    if post:
        return 'DETECTED_AFTER_EXECUTION'
    if pre and pre[0].get('decision') == 'deny' and policy.deny_verified:
        return 'BLOCKED'
    return UNKNOWN


def _outcome(post):
    if not post:
        return 'NO_POST_EVENT'
    code = post[0].get('exit_code')
    if code is None:
        return 'POST_OBSERVED_STATUS_UNKNOWN'
    return 'EXIT_ZERO' if code == 0 else 'EXIT_NONZERO'


def _iso(ns):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ns // 1_000_000_000))


# ---------------------------------------------------------------- summary
def _strings(value, out):
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            _strings(k, out)
            _strings(v, out)
    elif isinstance(value, list):
        for v in value:
            _strings(v, out)


RESIDUAL_RISKS = ('hooks are cooperative: a full-access process can bypass or disable them',
                  'same-UID code can tamper with the local store and key',
                  'post-hoc inventory cannot prevent or catch transient changes')
_CONSTANT_TEXT = ' '.join(list(RULES.values()) + list(RESIDUAL_RISKS) + list(_RANK) + [
    'NOT_REQUIRED DETECTED_AFTER_EXECUTION BLOCKED NO_POST_EVENT POST_OBSERVED_STATUS_UNKNOWN EXIT_ZERO',
    'EXIT_NONZERO ALLOWLIST_SYNOPSIS_ONLY PENDING_INDEPENDENT_REVIEW AUDIT_INCOMPLETE OBSERVED_EVENTS_CONSISTENT',
    'INTERRUPTED_NO_POST MISSING_POST MISSING_PRE DUPLICATE_PRE DUPLICATE_POST POST_BEFORE_PRE PRE_POST_MISMATCH',
    'TORN_RECORD UNREADABLE_RECORD INTEGRITY_MISMATCH SEQUENCE_ANOMALY MALFORMED_EVENT OVERSIZE NOT_UTF8_JSON',
    'NOT_HOOK_EVENT MISSING_FIELD INVALID_ID INVALID_FIELD HOOK_COVERAGE_NOT_VERIFIED PINS_NOT_PROVIDED',
    'PRE_EXECUTION_DENY_NOT_VERIFIED PIN_CHANGE_SEPARATE_APPROVAL_REQUIRED INVENTORY_DRIFT_OR_MISSING',
    'INVENTORY_NOT_PROVIDED EVENT_GAPS ACTIONS_REQUIRE_REVIEW COMPOUND_SHELL OTHER_PROGRAM UNKNOWN_TOOL',
    'UNPARSEABLE INVALID_CWD INVALID_PATH CODEX_ROOT PROTECTED_ROOT git-dir-override inline-code secret-hint',
    'llmtool.codex_approval_audit'])


def leak_check(text, records, policy, allowed=()):
    """Fail-closed self-check: no raw string piece (>=8 chars) from any event may appear."""
    vocab = (set(KNOWN_PROGRAMS) | KNOWN_GIT | SHELL_TOOLS | PATCH_TOOLS | set(RULES) |
             set(re.findall(r'[A-Za-z0-9_.<>-]+', _CONSTANT_TEXT)) | set(allowed))
    raw = []
    for rec in records:
        for name in ('tool_input', 'tool_response', 'cwd', 'tool_name'):
            _strings(rec.get(name), raw)
    raw.extend((policy.codex_root, policy.home) + tuple(policy.protected_roots) + tuple(policy.pinned_paths))
    pieces = set()
    for item in raw:
        pieces.add(item)
        pieces.update(re.split(r'[\s=:,;"\'/]+', item))
    ids = {rec.get(k) for rec in records for k in ('session_id', 'turn_id', 'tool_use_id')}
    return sorted({hashlib.sha256(p.encode('utf-8', 'surrogatepass')).hexdigest()[:12]
                   for p in pieces if len(p) >= 8 and p not in vocab and p not in ids and p in text})


def summarize(store, task_id, source_sha, policy, pins=None, inventory_drift=None, forbidden=()):
    """Deterministic, allowlisted per-task summary. Raises if the self-check finds a leak."""
    if not (isinstance(task_id, str) and _TASK.fullmatch(task_id)) or not (
            isinstance(source_sha, str) and _SHA.fullmatch(source_sha)):
        raise AuditError('invalid task id or source sha')
    key, records, gaps, store_mac = read_store(store, forbidden)
    actions, action_gaps = correlate(records)
    gaps = gaps + action_gaps
    rows = []
    for index, action in enumerate(actions, 1):
        first = (action['pre'] or action['post'])[0]
        # A pre/post mismatch is judged by the worse of the two inputs.
        kind, classification, rule, synopsis, flags = max(
            (classify(r['tool_name'], r.get('tool_input'), r['cwd'], policy) for r in action['pre'] + action['post']),
            key=lambda c: _RANK[c[1]])
        if action['problems'] and classification == ROUTINE:
            classification = UNKNOWN
        rows.append({
            'action': index, 'time_utc': _iso(first['received_ns']), 'session_id': first['session_id'],
            'turn_id': first['turn_id'], 'tool_use_id': first['tool_use_id'], 'tool_kind': kind,
            'synopsis': synopsis, 'flags': flags, 'cwd_class': _place(_norm(first['cwd'], '/'), policy),
            'pre': len(action['pre']) == 1, 'post': len(action['post']) == 1, 'outcome': _outcome(action['post']),
            'classification': classification, 'inferred': classification in (ROUTINE, WOULD_PROMPT),
            'rule_id': rule, 'reason': RULES[rule],
            'enforcement': _enforcement(classification, action['pre'], action['post'], policy),
            'problems': action['problems'],
            'event_refs': [r['ref'] for r in action['pre'] + action['post']],
            'redaction': 'ALLOWLIST_SYNOPSIS_ONLY'})
    blockers = []
    if not policy.coverage_verified:
        blockers.append('HOOK_COVERAGE_NOT_VERIFIED')
    if not policy.deny_verified:
        blockers.append('PRE_EXECUTION_DENY_NOT_VERIFIED')
    pin_changes = sorted(pins['changed']) if pins else ['PINS_NOT_PROVIDED']
    if pin_changes:
        blockers.append('PIN_CHANGE_SEPARATE_APPROVAL_REQUIRED' if pins else 'PINS_NOT_PROVIDED')
    drift = sorted(inventory_drift) if inventory_drift is not None else ['INVENTORY_NOT_PROVIDED']
    if drift:
        blockers.append('INVENTORY_DRIFT_OR_MISSING')
    if gaps:
        blockers.append('EVENT_GAPS')
    if any(r['classification'] in (UNAUTHORIZED, SEPARATE, UNKNOWN) for r in rows):
        blockers.append('ACTIONS_REQUIRE_REVIEW')
    summary = {
        'schema_version': SCHEMA_VERSION, 'task_id': task_id, 'source_sha': source_sha,
        'status': 'AUDIT_INCOMPLETE' if blockers else 'OBSERVED_EVENTS_CONSISTENT',
        'blockers': blockers, 'actions': rows, 'gaps': gaps, 'pin_changes': pin_changes,
        'inventory_drift': drift, 'store_mac': store_mac,
        'collector': 'llmtool.codex_approval_audit v%d' % SCHEMA_VERSION,
        'collector_sha256': file_digest(os.path.abspath(__file__)),
        'rules': {k: RULES[k] for k in sorted({r['rule_id'] for r in rows})},
        'residual_risks': list(RESIDUAL_RISKS),
        'reviewer_verdict': 'PENDING_INDEPENDENT_REVIEW'}
    text = json.dumps(summary, sort_keys=True, indent=1, ensure_ascii=True) + '\n'
    synopses = tuple(r['synopsis'] for r in rows)  # Built only from allowlisted words.
    if leak_check(text, records, policy, allowed=(task_id, source_sha) + synopses):
        raise AuditError('summary self-check found raw event content')
    return text


# ------------------------------------------------------- A2 pins, A1 inventory
def file_digest(path):
    """sha256 of a regular file opened without following symlinks; else a status word."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return 'MISSING'
    except OSError:
        return 'UNREADABLE_OR_SYMLINK'
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return 'NOT_REGULAR'
        return hashlib.sha256(_read_all(fd)).hexdigest()
    finally:
        os.close(fd)


def pin_files(labelled):
    """{label: digest} for Codex config, hooks, collector and binary paths."""
    return {label: file_digest(path) for label, path in sorted(labelled.items())}


def compare_pins(start, end):
    changed = sorted(label for label in set(start) | set(end)
                     if start.get(label) != end.get(label) or start.get(label) in
                     ('UNREADABLE_OR_SYMLINK', 'NOT_REGULAR'))
    return {'start': start, 'end': end, 'changed': changed}


def run_git(args, cwd):
    """Read-only git query with optional locks disabled and no prompts."""
    env = {'PATH': '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin', 'LC_ALL': 'C', 'GIT_OPTIONAL_LOCKS': '0',
           'GIT_TERMINAL_PROMPT': '0', 'HOME': os.environ.get('HOME', '/nonexistent')}
    done = subprocess.run(['git', '-C', cwd] + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env=env, timeout=GIT_TIMEOUT_SECONDS, check=False)
    return done.returncode, done.stdout


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def inventory(roots, common_dir, git=run_git, own_worktree=None):
    """Read-only snapshot of peer/canonical roots and shared Git metadata.

    roots: {label: path}; never writes. Per root: HEAD, branch, index entries
    and status (incl. ignored). Shared: refs, config, hooks, worktree metadata.
    """
    snap = {}
    for label, path in sorted(roots.items()):
        for name, args in (('head', ['rev-parse', 'HEAD']), ('branch', ['symbolic-ref', '-q', 'HEAD']),
                           ('index', ['ls-files', '-s', '-z']),
                           ('status', ['status', '--porcelain=v1', '-z', '--untracked-files=all',
                                       '--ignored=matching'])):
            code, out = git(args, path)
            snap['%s.%s' % (label, name)] = _sha(out) if code == 0 else 'GIT_EXIT_%d' % code
    code, out = git(['for-each-ref', '--format=%(refname) %(objectname)'], common_dir)
    refs = dict(line.split(' ', 1) for line in out.decode('utf-8', 'replace').splitlines() if ' ' in line) \
        if code == 0 else {'GIT_EXIT': str(code)}
    for ref, obj in sorted(refs.items()):
        snap['ref:' + ref] = obj
    snap['common.config'] = file_digest(os.path.join(common_dir, 'config'))
    hooks = os.path.join(common_dir, 'hooks')
    names = sorted(os.listdir(hooks)) if os.path.isdir(hooks) and not os.path.islink(hooks) else []
    snap['common.hooks'] = _sha(_canonical({n: file_digest(os.path.join(hooks, n)) for n in names}))
    trees = os.path.join(common_dir, 'worktrees')
    for name in sorted(os.listdir(trees)) if os.path.isdir(trees) and not os.path.islink(trees) else []:
        files = ('gitdir', 'locked', 'commondir') if name == own_worktree else (
            'gitdir', 'locked', 'commondir', 'HEAD', 'index')
        for item in files:
            snap['worktree:%s/%s' % (name, item)] = file_digest(os.path.join(trees, name, item))
    return snap


def compare_inventory(pre, post, allowed_refs=()):
    """Drift labels (never values). Allowed refs are the task's own branch refs."""
    return sorted(label for label in set(pre) | set(post) if pre.get(label) != post.get(label)
                  and not (label.startswith('ref:') and label[4:] in allowed_refs))


# --------------------------------------------------------------------- CLI
def _policy(path):
    with open(path, 'rb') as handle:
        data = json.loads(handle.read(MAX_EVENT_BYTES).decode('utf-8'))
    return AuditPolicy(data['codex_root'], data['home'], tuple(data['protected_roots']),
                       tuple(data.get('allowed_paths', ())), tuple(data.get('pinned_paths', ())),
                       bool(data.get('deny_verified', False)), bool(data.get('coverage_verified', False)))


def main(argv=None):
    """init|record|summarize. record reads one hook event on stdin and prints nothing."""
    import argparse
    parser = argparse.ArgumentParser(prog='python -m llmtool.codex_approval_audit')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'record', 'summarize'):
        p = sub.add_parser(name)
        p.add_argument('--store', required=True)
        p.add_argument('--forbid', action='append', default=[])
        if name == 'summarize':
            p.add_argument('--task', required=True)
            p.add_argument('--source-sha', required=True)
            p.add_argument('--policy', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            init_store(args.store, args.forbid)
        elif args.command == 'record':
            record_event(args.store, sys.stdin.buffer.read(MAX_EVENT_BYTES + 1), forbidden=args.forbid)
        else:
            sys.stdout.write(summarize(args.store, args.task, args.source_sha, _policy(args.policy),
                                       forbidden=args.forbid))
    except Exception:
        # Fixed text only, never event content or a traceback. Codex's handling of a failing hook is NOT_VERIFIED.
        sys.stderr.write('codex-approval-audit: %s failed\n' % args.command)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
