"""Issue #135 task-scoped Codex tool-event audit collector (NOT an activation).

The collector is cooperative and observe-only. A Codex ``PreToolUse`` /
``PostToolUse`` command hook pipes each event to ``record`` and the collector
appends it to a private local store: a 0700 directory with 0600 files, kept
outside every repository and the Git common-dir. ``summarize`` then builds a
deterministic per-task summary for Git. Every exported string must come from
a fixed reviewed vocabulary, a keyed HMAC reference, a timestamp or the
validated task/source provenance. Raw argv, environment, paths, identifiers,
labels, tokens and stdout are never exported.

Truthfulness rules:
- ``WOULD_PROMPT_UNDER_OLD_PROFILE`` is an inference from a reviewed static
  mapping of the prior workspace-write + on-request profile. It is never a
  genuine owner approval.
- ``BLOCKED`` requires a collector deny record, natively verified deny
  enforcement (``AuditPolicy.deny_verified``) and no PostToolUse. Anything
  else observed after the fact is ``DETECTED_AFTER_EXECUTION``, or
  ``UNKNOWN`` when there is no post event.
- ``OBSERVED_EVENTS_CONSISTENT`` requires boolean coverage/deny verification,
  complete successful pins, a complete successful inventory without drift,
  the expected sessions with tool events and session ends, a complete store
  and no action needing review. Failure or absence is never "unchanged".
- Hook coverage, trust and fail-open behaviour of the installed Codex are
  NOT_VERIFIED until a reviewed native smoke. A full-access Codex process can
  bypass hooks entirely, and same-UID code can tamper with the store and key.
  Neither is prevented here.

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
import select
import shlex
import stat
import subprocess
import sys
import time
from dataclasses import dataclass

from .errors import LLMToolError

SCHEMA_VERSION = 2
MAX_EVENT_BYTES = 1024 * 1024
RESPONSE_CAP_BYTES = 64 * 1024
MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_STORE_BYTES = 64 * 1024 * 1024
MAX_EVENT_RECORDS = 50000
OVERFLOW_RESERVE = 64 * 1024
OVERFLOW_RECORDS = 256
MAX_SEQ_BYTES = 128
MAX_POLICY_BYTES = 64 * 1024
MAX_PIN_BYTES = 512 * 1024 * 1024
MAX_GIT_OUTPUT = 32 * 1024 * 1024
MAX_LISTING = 256
MAX_INVENTORY_ENTRIES = 100000
MAX_EXPECTED_SESSIONS = 64
LOCK_TIMEOUT_SECONDS = 5.0
GIT_TIMEOUT_SECONDS = 10
KEY_BYTES = 32
REF_HEX = 32
PUBLIC_REF_HEX = 16
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
_TASK = re.compile(r'(issue|pr)-[0-9]{1,6}')
_SHA = re.compile(r'[0-9a-f]{40}')
_DIGEST = re.compile(r'[0-9a-f]{64}')
_HEX_REF = re.compile(r'[0-9a-f]{16,64}')
_TIME = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z')
_SEQ_TEXT = re.compile(rb'(0|[1-9][0-9]{0,9}) ([0-9a-f]{64})')

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
# Git global options. Value options consume the next argument unless written as --opt=value.
GIT_VALUE_GLOBALS = frozenset({'-C', '-c', '--git-dir', '--work-tree', '--namespace', '--exec-path',
                               '--config-env', '--super-prefix'})
GIT_SAFE_GLOBALS = frozenset({'-C', '--no-pager', '-P', '--no-optional-locks', '--literal-pathspecs',
                              '--no-replace-objects'})
GIT_CONFIG_GLOBALS = frozenset({'-c', '--config-env'})
GIT_REPO_GLOBALS = frozenset({'--git-dir', '--work-tree', '--namespace', '--exec-path', '--super-prefix'})
# Options that make a read-only subcommand write a file, run an external program or a pager.
GIT_READ_RISKY = ('--output', '--ext-diff', '--textconv', '--open-files-in-pager', '--filters', '--exec',
                  '--upload-pack', '--receive-pack', '--paginate')
BENIGN_ENV = frozenset({'COLUMNS', 'LANG', 'LC_ALL', 'LC_CTYPE', 'NO_COLOR', 'TERM', 'TZ'})
_ASSIGN = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=(.*)', re.S)
_SHELL_META = re.compile(r'[;&|<>`$(){}\n]')
_SECRET_HINT = re.compile(r'(?i)(auth\.json|(^|/)\.ssh(/|$)|\.netrc|(^|/)\.aws(/|$)|keychain|'
                          r'(token|secret|passw(or)?d|api[_-]?key|credential)[A-Za-z_]*=)')

RULES = {
    'R00': 'tool name not in the reviewed mapping',
    'R01': 'read-only program inside the task root',
    'R02': 'workspace write/build inside the task root (no prompt under the old sandbox)',
    'R03': 'git read-only subcommand',
    'R10': 'git write to the shared common-dir outside the old writable root',
    'R11': 'git/gh network operation (old profile had no network)',
    'R12': 'write to a path outside the task root but not a protected root',
    'R20': 'destructive, repository/config-overriding or unknown git operation',
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
    'R33': 'environment assignment, pager or unrecognised global option alters the command',
    'R34': 'git read subcommand with an output-file, external-program or pager option',
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

# A2 pin labels and A1 inventory labels are a fixed reviewed vocabulary.
REQUIRED_PINS = ('codex.binary', 'codex.config', 'codex.hooks', 'collector.module')
PIN_LABELS = frozenset(REQUIRED_PINS + ('codex.project_config', 'codex.trust_state', 'collector.hook_adapter'))
ROOT_LABELS = frozenset({'canonical', 'claude', 'codex', 'task'})
ROOT_ITEMS = ('head', 'branch', 'index', 'status')
REQUIRED_INVENTORY = ('common.config', 'common.hooks', 'shared.refs')
FAILURE_WORDS = frozenset({'MISSING', 'UNREADABLE_OR_SYMLINK', 'NOT_REGULAR', 'TOO_LARGE', 'TOO_MANY'})
MALFORMED_REASONS = frozenset({'OVERSIZE', 'NOT_UTF8_JSON', 'NOT_HOOK_EVENT', 'MISSING_FIELD', 'INVALID_ID',
                               'INVALID_FIELD', 'RECORD_TOO_LARGE'})


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


def _check_policy(policy):
    if not isinstance(policy, AuditPolicy):
        raise AuditError('audit policy is malformed')
    paths = (policy.codex_root, policy.home)
    if (type(policy.deny_verified) is not bool
            or type(policy.coverage_verified) is not bool
            or not all(isinstance(group, (tuple, list)) for group in
                       (policy.protected_roots, policy.allowed_paths, policy.pinned_paths))
            or not all(isinstance(p, str) and p.startswith('/') for p in
                       paths + tuple(policy.protected_roots) + tuple(policy.pinned_paths))
            or not all(isinstance(p, str) for p in policy.allowed_paths)):
        raise AuditError('audit policy is malformed')


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
    try:
        fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
    except FileNotFoundError:
        raise AuditError('audit store file is missing') from None
    try:
        _check_private(os.fstat(fd), False)
    except Exception:
        os.close(fd)
        raise
    return fd


def _read_limited(fd, limit):
    """Read at most ``limit`` bytes; a longer file is a fixed-text failure."""
    chunks, total = [], 0
    while True:
        chunk = os.read(fd, min(1 << 16, limit + 1 - total))
        if not chunk:
            return b''.join(chunks)
        total += len(chunk)
        if total > limit:
            raise AuditError('audit file exceeds its size limit')
        chunks.append(chunk)


def _lock(fd, mode):
    """Bounded flock acquisition; never blocks past LOCK_TIMEOUT_SECONDS."""
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            pass
        if time.monotonic() >= deadline:
            raise AuditError('audit store lock timed out')
        time.sleep(0.005)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def _mac(key, label, data):
    return hmac.new(key, label + b'\0' + data, hashlib.sha256).hexdigest()


def public_ref(key, kind, value):
    """Keyed pseudonymous reference for an identifier exported to Git."""
    return _mac(key, b'public-' + kind.encode(), value.encode('utf-8', 'surrogatepass'))[:PUBLIC_REF_HEX]


def _seq_text(key, number):
    digits = str(number).encode()
    return digits + b' ' + _mac(key, b'seq', digits).encode()


def _parse_seq(key, raw):
    match = _SEQ_TEXT.fullmatch(raw) if raw is not None else None
    if not match or not hmac.compare_digest(match.group(2).decode(), _mac(key, b'seq', match.group(1))):
        return None
    return int(match.group(1))


def _write_seq(fd, key, number):
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, _seq_text(key, number))
    os.fsync(fd)


def init_store(store, forbidden=()):
    """Create a new private store with a fresh random HMAC key (exclusive)."""
    dir_fd = _open_store(store, forbidden, create=True)
    try:
        key = os.urandom(KEY_BYTES)
        fd = _open_file(dir_fd, KEY, os.O_WRONLY, create=True)
        try:
            os.write(fd, key)
            os.fsync(fd)
        finally:
            os.close(fd)
        fd = _open_file(dir_fd, SEQ, os.O_WRONLY, create=True)
        try:
            _write_seq(fd, key, 0)
        finally:
            os.close(fd)
        for name in (LOCK, EVENTS):
            os.close(_open_file(dir_fd, name, os.O_WRONLY, create=True))
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _key(dir_fd):
    fd = _open_file(dir_fd, KEY, os.O_RDONLY)
    try:
        key = _read_limited(fd, KEY_BYTES)
    finally:
        os.close(fd)
    if len(key) != KEY_BYTES:
        raise AuditError('audit key is invalid')
    return key


def _sealed(key, body):
    return _canonical(dict(body, mac=_mac(key, b'record', _canonical(body)))) + b'\n'


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
    except (UnicodeDecodeError, ValueError, RecursionError):
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
            _lock(lock_fd, fcntl.LOCK_EX)
            seq_fd = _open_file(dir_fd, SEQ, os.O_RDWR)
            try:
                events_fd = _open_file(dir_fd, EVENTS, os.O_WRONLY | os.O_APPEND)
            except Exception:
                os.close(seq_fd)
                raise
            try:
                current = _parse_seq(key, _read_limited(seq_fd, MAX_SEQ_BYTES))
                if current is None:
                    raise AuditError('audit sequence state is invalid')
                seq = current + 1
                data = _sealed(key, dict(body, seq=seq))
                if len(data) > MAX_RECORD_BYTES:
                    data = _sealed(key, {'v': SCHEMA_VERSION, 'kind': 'malformed', 'reason': 'RECORD_TOO_LARGE',
                                         'size': len(raw), 'received_ns': received, 'seq': seq})
                size = os.fstat(events_fd).st_size
                if seq > MAX_EVENT_RECORDS or size + len(data) > MAX_STORE_BYTES:
                    # A small marker keeps the loss visible; past the reserve the append fails closed.
                    data = _sealed(key, {'v': SCHEMA_VERSION, 'kind': 'overflow', 'received_ns': received,
                                         'seq': seq})
                    if (seq > MAX_EVENT_RECORDS + OVERFLOW_RECORDS
                            or size + len(data) > MAX_STORE_BYTES + OVERFLOW_RESERVE):
                        raise AuditError('audit store is full')
                if os.write(events_fd, data) != len(data):
                    raise AuditError('audit append was short')
                os.fsync(events_fd)
                _write_seq(seq_fd, key, seq)
            finally:
                os.close(events_fd)
                os.close(seq_fd)
        finally:
            os.close(lock_fd)  # Closing releases the flock.
    finally:
        os.close(dir_fd)
    return seq


def _snapshot(dir_fd):
    """Coherent (seq, events) bytes read under a shared lock; seq is None when missing or oversize."""
    lock_fd = _open_file(dir_fd, LOCK, os.O_RDWR)
    try:
        _lock(lock_fd, fcntl.LOCK_SH)
        try:
            seq_fd = _open_file(dir_fd, SEQ, os.O_RDONLY)
        except AuditError:
            seq_raw = None
        else:
            try:
                seq_raw = _read_limited(seq_fd, MAX_SEQ_BYTES)
            except AuditError:
                seq_raw = None
            finally:
                os.close(seq_fd)
        fd = _open_file(dir_fd, EVENTS, os.O_RDONLY)
        try:
            raw = _read_limited(fd, MAX_STORE_BYTES + OVERFLOW_RESERVE)
        finally:
            os.close(fd)
    finally:
        os.close(lock_fd)
    return seq_raw, raw


def read_store(store, forbidden=()):
    """Return (key, records, gaps, store_mac). Integrity and completeness problems become gaps."""
    dir_fd = _open_store(store, forbidden)
    try:
        key = _key(dir_fd)
        seq_raw, raw = _snapshot(dir_fd)
    finally:
        os.close(dir_fd)
    lines = raw.split(b'\n')
    if len(lines) > MAX_EVENT_RECORDS + OVERFLOW_RECORDS + 1:
        raise AuditError('audit store has too many records')
    records, gaps = [], []
    if lines[-1]:
        gaps.append({'gap': 'TORN_RECORD'})
    expected, last = 1, 0
    for data in lines[:-1]:
        if len(data) > MAX_RECORD_BYTES:
            gaps.append({'gap': 'RECORD_TOO_LARGE'})
            continue
        try:
            line = json.loads(data.decode('utf-8'))
            mac = line.pop('mac')
        except (UnicodeDecodeError, ValueError, KeyError, AttributeError, TypeError, RecursionError):
            gaps.append({'gap': 'UNREADABLE_RECORD'})
            continue
        if not hmac.compare_digest(str(mac), _mac(key, b'record', _canonical(line))):
            gaps.append({'gap': 'INTEGRITY_MISMATCH'})
            continue
        seq = line.get('seq')
        if not isinstance(seq, int) or isinstance(seq, bool):
            gaps.append({'gap': 'SEQUENCE_ANOMALY', 'seq': None})
            continue
        if seq != expected:
            gaps.append({'gap': 'SEQUENCE_ANOMALY', 'seq': seq})
        expected, last = seq + 1, max(last, seq)
        line['ref'] = _mac(key, b'ref', data)[:REF_HEX]
        records.append(line)
        if line.get('kind') == 'malformed':
            reason = line.get('reason') if line.get('reason') in MALFORMED_REASONS else 'UNREADABLE_RECORD'
            gaps.append({'gap': 'MALFORMED_EVENT', 'reason': reason, 'seq': seq})
        elif line.get('kind') == 'overflow':
            gaps.append({'gap': 'STORE_OVERFLOW', 'seq': seq})
    frontier = _parse_seq(key, seq_raw)
    if frontier is None:
        gaps.append({'gap': 'SEQ_STATE_INVALID'})
    elif last < frontier:
        gaps.append({'gap': 'TAIL_TRUNCATED'})
    elif last > frontier:
        gaps.append({'gap': 'SEQ_FRONTIER_BEHIND'})
    return key, records, gaps, _mac(key, b'store', (seq_raw or b'') + b'\0' + raw)


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


def _classify_git(args, flags):
    globals_, i = [], 0
    while i < len(args) and args[i].startswith('-'):
        globals_.append(args[i].split('=', 1)[0])
        i += 2 if args[i] in GIT_VALUE_GLOBALS else 1
    sub = args[i] if i < len(args) else ''
    rest = args[i:]
    if '-C' in globals_:
        flags.append('cwd-override')
    if GIT_CONFIG_GLOBALS.intersection(globals_):
        flags.append('config-override')
    if GIT_REPO_GLOBALS.intersection(globals_):
        flags.append('git-dir-override')
    unknown_global = [g for g in globals_ if g not in GIT_VALUE_GLOBALS and g not in GIT_SAFE_GLOBALS]
    if unknown_global:
        flags.append('unknown-option')
    force = any(a in ('-f', '--force', '--force-with-lease', '-D', '--hard', '--delete') or a.startswith('+')
                for a in rest)
    if force:
        flags.append('force')
    reads = sub in GIT_READ or (sub == 'branch' and all(a in ('branch', '--show-current', '-a', '-r', '--list',
                                                               '-v') for a in rest)) or rest == ['remote', '-v']
    risky = [a for a in rest[1:] if a.startswith(GIT_READ_RISKY) or (sub == 'grep' and a.startswith('-O'))]
    if ('config-override' in flags or 'git-dir-override' in flags or sub in GIT_NOGO or sub not in KNOWN_GIT
            or (sub == 'switch' and 'main' in rest)):
        result = (UNAUTHORIZED, 'R20')
    elif unknown_global:
        result = (UNKNOWN, 'R33')
    elif reads and risky:
        flags.append('output-or-exec-option')
        result = (UNKNOWN, 'R34')
    elif reads:
        result = (ROUTINE, 'R03')
    elif force:
        result = (UNAUTHORIZED, 'R20')
    elif sub in GIT_NETWORK:
        result = (WOULD_PROMPT, 'R11')
    else:
        result = (WOULD_PROMPT, 'R10')
    return result, 'git ' + (sub if sub in KNOWN_GIT else '<OTHER>')


def _classify_command(argv, cwd, policy):
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
        result, synopsis = _classify_git(args, flags)
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


def _classify_argv(argv, cwd, policy):
    assignments = []
    while argv and (posixpath.basename(argv[0]) in WRAPPERS or _ASSIGN.fullmatch(argv[0])):
        is_assignment = _ASSIGN.fullmatch(argv[0]) is not None
        if is_assignment:
            assignments.append(argv[0])
        argv = argv[1:]
        if not is_assignment and argv and argv[0].startswith('-'):
            argv = argv[1:]
    if not argv:
        return (UNKNOWN, 'R51'), '<EMPTY>', []
    result, synopsis, flags = _classify_command(argv, cwd, policy)
    if assignments:
        # Environment assignments are never discarded: GIT_* redirects git, others may alter any program.
        names = [_ASSIGN.fullmatch(a).group(1) for a in assignments]
        extra = []
        if any(_SECRET_HINT.search(a) for a in assignments):
            extra.append((UNAUTHORIZED, 'R24'))
            flags = flags + ['secret-hint']
        if any(n.startswith('GIT_') for n in names):
            extra.append((UNAUTHORIZED, 'R20'))
        elif any(n not in BENIGN_ENV for n in names):
            extra.append((UNKNOWN, 'R33'))
        if any(n not in BENIGN_ENV for n in names):
            flags = flags + ['env-override']
        values = [_norm(a.split('=', 1)[1], cwd) for a in assignments if a.split('=', 1)[1].startswith(('/', '.'))]
        extra += _path_rules([v for v in values if v], False, policy)
        result = _verdict(extra + [result])
    return result, synopsis, flags


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
    if pre and pre[0].get('decision') == 'deny' and policy.deny_verified is True:
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
def _strings(value, out, keys=True):
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            if keys:
                _strings(k, out, keys)
            _strings(v, out, keys)
    elif isinstance(value, list):
        for v in value:
            _strings(v, out, keys)


RESIDUAL_RISKS = ('hooks are cooperative: a full-access process can bypass or disable them',
                  'same-UID code can tamper with the local store and key',
                  'post-hoc inventory cannot prevent or catch transient changes')
NOT_VERIFIED = ('real Codex tool names and hook coverage', 'pre-execution deny enforcement',
                'hook failure (fail-open) behaviour',
                'A1 content of already-dirty or ignored files is not inventoried')
BLOCKERS = ('HOOK_COVERAGE_NOT_VERIFIED', 'PRE_EXECUTION_DENY_NOT_VERIFIED', 'PINS_NOT_PROVIDED',
            'PINS_INCOMPLETE', 'PINS_NOT_VERIFIED', 'PIN_CHANGE_SEPARATE_APPROVAL_REQUIRED',
            'INVENTORY_NOT_PROVIDED', 'INVENTORY_NOT_VERIFIED', 'INVENTORY_DRIFT', 'EXPECTED_SESSIONS_NOT_PROVIDED',
            'UNEXPECTED_SESSION', 'EXPECTED_SESSION_NOT_OBSERVED', 'SESSION_END_NOT_OBSERVED',
            'NO_TOOL_EVENTS_OBSERVED', 'EVENT_GAPS', 'ACTIONS_REQUIRE_REVIEW')
_EXPORT_WORDS = frozenset(
    set(RULES) | set(RULES.values()) | set(RESIDUAL_RISKS) | set(NOT_VERIFIED) | set(BLOCKERS) | set(_RANK) |
    PIN_LABELS | MALFORMED_REASONS | FAILURE_WORDS | KNOWN_PROGRAMS | {'git ' + s for s in KNOWN_GIT} |
    {'%s.%s' % (r, i) for r in ROOT_LABELS for i in ROOT_ITEMS} |
    {'common.config', 'common.hooks', 'shared.refs', 'worktree.metadata', 'other.metadata'} |
    {'NOT_REQUIRED', 'DETECTED_AFTER_EXECUTION', 'BLOCKED', 'NO_POST_EVENT', 'POST_OBSERVED_STATUS_UNKNOWN',
     'EXIT_ZERO', 'EXIT_NONZERO', 'ALLOWLIST_SYNOPSIS_ONLY', 'PENDING_INDEPENDENT_REVIEW', 'AUDIT_INCOMPLETE',
     'OBSERVED_EVENTS_CONSISTENT', 'INTERRUPTED_NO_POST', 'MISSING_POST', 'MISSING_PRE', 'DUPLICATE_PRE',
     'DUPLICATE_POST', 'POST_BEFORE_PRE', 'PRE_POST_MISMATCH', 'TORN_RECORD', 'UNREADABLE_RECORD',
     'INTEGRITY_MISMATCH', 'SEQUENCE_ANOMALY', 'MALFORMED_EVENT', 'STORE_OVERFLOW', 'SEQ_STATE_INVALID',
     'TAIL_TRUNCATED', 'SEQ_FRONTIER_BEHIND', 'git <OTHER>', '<COMPOUND_SHELL>', '<OTHER_PROGRAM>',
     '<UNKNOWN_TOOL>', '<UNPARSEABLE>', '<EMPTY>', '<INVALID_CWD>', '<INVALID_PATH>', '<CODEX_ROOT>',
     '<CODEX_ROOT>/...', '<PROTECTED_ROOT>', '<HOME>/...', '<OUTSIDE>', 'apply_patch', 'mcp', 'shell', 'patch',
     'unknown', 'compound', 'cwd-override', 'config-override', 'git-dir-override', 'unknown-option',
     'output-or-exec-option', 'env-override', 'force', 'recursive', 'inline-code', 'secret-hint',
     'llmtool.codex_approval_audit v%d' % SCHEMA_VERSION})
# Leak-check vocabulary: whole export words plus their sub-tokens, so a raw piece equal to a fixed word is fine.
_VOCAB = frozenset(_EXPORT_WORDS | {t for w in _EXPORT_WORDS for t in re.split(r'[^A-Za-z0-9]+', w) if t} |
                   {t for w in _EXPORT_WORDS for t in re.findall(r'[A-Za-z0-9_.<>-]+', w)})


def leak_check(text, records, policy, extra=()):
    """Fail-closed self-check: no raw string piece (>=8 chars) from any event, ID or input may appear.

    ``text`` is a string or a list of exported strings. Identifiers get no exemption.
    """
    texts = [text] if isinstance(text, str) else list(text)
    raw = []
    for rec in records:
        for name in ('tool_input', 'tool_response', 'cwd', 'tool_name', 'session_id', 'turn_id', 'tool_use_id'):
            _strings(rec.get(name), raw)
    raw.extend((policy.codex_root, policy.home) + tuple(policy.protected_roots) + tuple(policy.pinned_paths))
    raw.extend(extra)
    pieces = set()
    for item in raw:
        pieces.add(item)
        pieces.update(re.split(r'[\s=:,;"\'/]+', item))
    return sorted({hashlib.sha256(p.encode('utf-8', 'surrogatepass')).hexdigest()[:12]
                   for p in pieces if len(p) >= 8 and p not in _VOCAB and any(p in t for t in texts)})


def _pin_evidence(pins):
    if pins is None:
        return ['PINS_NOT_PROVIDED'], [], []
    if not isinstance(pins, dict) or not all(isinstance(pins.get(side), dict) for side in ('start', 'end')):
        raise AuditError('pin evidence is malformed')
    start, end = pins['start'], pins['end']
    for side in (start, end):
        if not all(label in PIN_LABELS and isinstance(value, str) for label, value in side.items()):
            raise AuditError('pin evidence is malformed')
    result = compare_pins(start, end)
    blockers = []
    if not set(REQUIRED_PINS) <= set(start) or not set(REQUIRED_PINS) <= set(end):
        blockers.append('PINS_INCOMPLETE')
    if result['unverified']:
        blockers.append('PINS_NOT_VERIFIED')
    if result['changed']:
        blockers.append('PIN_CHANGE_SEPARATE_APPROVAL_REQUIRED')
    return blockers, result['changed'], result['unverified']


def _category(label):
    """Fixed public category for an internal inventory label; never the raw label."""
    if label.startswith('ref:') or label == 'shared.refs':
        return 'shared.refs'
    if label.startswith('worktree:'):
        return 'worktree.metadata'
    if label in ('common.config', 'common.hooks'):
        return label
    root, _, item = label.partition('.')
    return label if root in ROOT_LABELS and item in ROOT_ITEMS else 'other.metadata'


def _inventory_evidence(inventory):
    if inventory is None:
        return ['INVENTORY_NOT_PROVIDED'], [], []
    if not isinstance(inventory, dict):
        raise AuditError('inventory evidence is malformed')
    pre, post, allowed = inventory.get('pre'), inventory.get('post'), inventory.get('allowed_refs', [])
    for snap in (pre, post):
        if (not isinstance(snap, dict) or len(snap) > MAX_INVENTORY_ENTRIES
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in snap.items())):
            raise AuditError('inventory evidence is malformed')
    if not isinstance(allowed, list) or not all(isinstance(r, str) and r.startswith('refs/heads/') for r in allowed):
        raise AuditError('inventory evidence is malformed')
    result = compare_inventory(pre, post, tuple(allowed))
    blockers = []
    complete = all(set(REQUIRED_INVENTORY) <= set(snap) and
                   any(_category(label) != 'other.metadata' and label.endswith('.head') for label in snap)
                   for snap in (pre, post))
    if not complete or result['unverified']:
        blockers.append('INVENTORY_NOT_VERIFIED')
    if result['drift']:
        blockers.append('INVENTORY_DRIFT')
    return (blockers, sorted({_category(label) for label in result['drift']}),
            sorted({_category(label) for label in result['unverified']}))


def _expected_sessions(sessions):
    if sessions is None:
        return None
    if (not isinstance(sessions, (list, tuple)) or not 1 <= len(sessions) <= MAX_EXPECTED_SESSIONS
            or not all(isinstance(s, str) and _ID.fullmatch(s) for s in sessions)):
        raise AuditError('expected sessions are malformed')
    return frozenset(sessions)


def _session_blockers(records, expected, rows):
    blockers = []
    hooks = [r for r in records if r.get('kind') == 'hook']
    if expected is None:
        blockers.append('EXPECTED_SESSIONS_NOT_PROVIDED')
    else:
        seen = {r['session_id'] for r in hooks}
        if seen - expected:
            blockers.append('UNEXPECTED_SESSION')
        if expected - seen:
            blockers.append('EXPECTED_SESSION_NOT_OBSERVED')
        for session in sorted(expected):
            tool = [r['seq'] for r in hooks if r['session_id'] == session and r['event'] in TOOL_EVENTS]
            ends = [r['seq'] for r in hooks if r['session_id'] == session and r['event'] in END_EVENTS]
            if not ends or (tool and max(ends) < max(tool)):
                blockers.append('SESSION_END_NOT_OBSERVED')
                break
    if not rows:
        blockers.append('NO_TOOL_EVENTS_OBSERVED')
    return blockers


def _check_export(summary, records, policy):
    """Every exported value must be fixed vocabulary, a keyed ref, a time or validated provenance."""
    values = []
    _strings({k: v for k, v in summary.items() if k not in ('task_id', 'source_sha')}, values, keys=False)
    if any(not (v in _EXPORT_WORDS or _HEX_REF.fullmatch(v) or _TIME.fullmatch(v)) for v in values):
        raise AuditError('summary contains a value outside the export vocabulary')
    if leak_check(values, records, policy):
        raise AuditError('summary self-check found raw event content')


def summarize(store, task_id, source_sha, policy, pins=None, inventory=None, expected_sessions=None,
              forbidden=()):
    """Deterministic, allowlisted per-task summary. Raises on malformed evidence or a self-check failure.

    pins: ``{'start': {label: digest}, 'end': {...}}`` with labels from PIN_LABELS (see pin_files).
    inventory: ``{'pre': snapshot, 'post': snapshot, 'allowed_refs': [...]}`` (see inventory).
    expected_sessions: the Codex session ids that belong to this task, from trusted task provenance.
    """
    if not (isinstance(task_id, str) and _TASK.fullmatch(task_id)) or not (
            isinstance(source_sha, str) and _SHA.fullmatch(source_sha)):
        raise AuditError('invalid task id or source sha')
    _check_policy(policy)
    pin_blockers, pin_changes, pins_unverified = _pin_evidence(pins)
    inventory_blockers, drift, inventory_unverified = _inventory_evidence(inventory)
    expected = _expected_sessions(expected_sessions)
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
            'action': index, 'time_utc': _iso(first['received_ns']),
            'session_ref': public_ref(key, 'session', first['session_id']),
            'turn_ref': public_ref(key, 'turn', first['turn_id']),
            'use_ref': public_ref(key, 'tool_use', first['tool_use_id']), 'tool_kind': kind,
            'synopsis': synopsis, 'flags': flags, 'cwd_class': _place(_norm(first['cwd'], '/'), policy),
            'pre': len(action['pre']) == 1, 'post': len(action['post']) == 1, 'outcome': _outcome(action['post']),
            'classification': classification, 'inferred': classification in (ROUTINE, WOULD_PROMPT),
            'rule_id': rule, 'reason': RULES[rule],
            'enforcement': _enforcement(classification, action['pre'], action['post'], policy),
            'problems': action['problems'],
            'event_refs': [r['ref'] for r in action['pre'] + action['post']],
            'redaction': 'ALLOWLIST_SYNOPSIS_ONLY'})
    blockers = []
    if policy.coverage_verified is not True:
        blockers.append('HOOK_COVERAGE_NOT_VERIFIED')
    if policy.deny_verified is not True:
        blockers.append('PRE_EXECUTION_DENY_NOT_VERIFIED')
    blockers += pin_blockers + inventory_blockers + _session_blockers(records, expected, rows)
    if gaps:
        blockers.append('EVENT_GAPS')
    if any(r['classification'] in (UNAUTHORIZED, SEPARATE, UNKNOWN) for r in rows):
        blockers.append('ACTIONS_REQUIRE_REVIEW')
    summary = {
        'schema_version': SCHEMA_VERSION, 'task_id': task_id, 'source_sha': source_sha,
        'status': 'AUDIT_INCOMPLETE' if blockers else 'OBSERVED_EVENTS_CONSISTENT',
        'blockers': blockers, 'actions': rows, 'gaps': gaps, 'event_count': len(records),
        'pin_changes': pin_changes, 'pins_unverified': pins_unverified,
        'inventory_drift': drift, 'inventory_unverified': inventory_unverified, 'store_mac': store_mac,
        'collector': 'llmtool.codex_approval_audit v%d' % SCHEMA_VERSION,
        'collector_sha256': file_digest(os.path.abspath(__file__)),
        'rules': {k: RULES[k] for k in sorted({r['rule_id'] for r in rows})},
        'residual_risks': list(RESIDUAL_RISKS), 'not_verified': list(NOT_VERIFIED),
        'reviewer_verdict': 'PENDING_INDEPENDENT_REVIEW'}
    _check_export(summary, records, policy)
    return json.dumps(summary, sort_keys=True, indent=1, ensure_ascii=True) + '\n'


# ------------------------------------------------------- A2 pins, A1 inventory
def file_digest(path):
    """Streamed sha256 of a regular file opened without following symlinks; else a status word."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return 'MISSING'
    except OSError:
        return 'UNREADABLE_OR_SYMLINK'
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return 'NOT_REGULAR'
        digest, total = hashlib.sha256(), 0
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                return digest.hexdigest()
            total += len(chunk)
            if total > MAX_PIN_BYTES:
                return 'TOO_LARGE'
            digest.update(chunk)
    except OSError:
        return 'UNREADABLE_OR_SYMLINK'
    finally:
        os.close(fd)


def pin_files(labelled):
    """{label: digest} for Codex config, hooks, collector and binary paths (labels from PIN_LABELS)."""
    return {label: file_digest(path) for label, path in sorted(labelled.items())}


def compare_pins(start, end):
    """Changed labels, and labels whose digest failed or is absent on either side (never 'unchanged')."""
    labels = set(start) | set(end)
    changed = sorted(label for label in labels if start.get(label) != end.get(label))
    unverified = sorted(label for label in labels
                        if not all(isinstance(s.get(label), str) and _DIGEST.fullmatch(s.get(label))
                                   for s in (start, end)))
    return {'start': start, 'end': end, 'changed': changed, 'unverified': unverified}


def _read_pipe(fd, limit, deadline):
    """Read a pipe to EOF within ``limit`` bytes and ``deadline``; None on overflow or timeout."""
    chunks, total = [], 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            return None
        chunk = os.read(fd, 1 << 16)
        if not chunk:
            return b''.join(chunks)
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)


def run_git(args, cwd):
    """Read-only git query: optional locks disabled, no prompts, bounded output and time."""
    env = {'PATH': '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin', 'LC_ALL': 'C', 'GIT_OPTIONAL_LOCKS': '0',
           'GIT_TERMINAL_PROMPT': '0', 'HOME': os.environ.get('HOME', '/nonexistent')}
    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    proc = subprocess.Popen(['git', '-C', cwd] + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env)
    try:
        out = _read_pipe(proc.stdout.fileno(), MAX_GIT_OUTPUT, deadline)
        if out is None:
            proc.kill()
            proc.wait()
            return -1, b''
        return proc.wait(timeout=max(0.0, deadline - time.monotonic())), out
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return -1, b''
    finally:
        proc.stdout.close()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _listing(path):
    """Sorted directory names, [] when absent, None past MAX_LISTING entries."""
    if not os.path.isdir(path) or os.path.islink(path):
        return []
    names = []
    with os.scandir(path) as entries:
        for entry in entries:
            names.append(entry.name)
            if len(names) > MAX_LISTING:
                return None
    return sorted(names)


def inventory(roots, common_dir, git=run_git, own_worktree=None):
    """Read-only snapshot of peer/canonical roots and shared Git metadata.

    roots: {label: path} with labels from ROOT_LABELS; never writes. Per root: HEAD,
    branch, index entries and status (incl. ignored). Shared: refs, config, hooks and
    worktree metadata. Status/index hashes do not reveal content edits to files that
    were already dirty or ignored; that A1 criterion remains NOT_VERIFIED.
    """
    if not all(label in ROOT_LABELS for label in roots):
        raise AuditError('inventory root label is not in the reviewed vocabulary')
    snap = {}
    for label, path in sorted(roots.items()):
        for name, args in (('head', ['rev-parse', 'HEAD']), ('branch', ['symbolic-ref', '-q', 'HEAD']),
                           ('index', ['ls-files', '-s', '-z']),
                           ('status', ['status', '--porcelain=v1', '-z', '--untracked-files=all',
                                       '--ignored=matching'])):
            code, out = git(args, path)
            snap['%s.%s' % (label, name)] = _sha(out) if code == 0 else 'GIT_EXIT_%d' % code
    code, out = git(['for-each-ref', '--format=%(refname) %(objectname)'], common_dir)
    lines = out.decode('utf-8', 'replace').splitlines() if code == 0 else []
    snap['shared.refs'] = ('GIT_EXIT_%d' % code if code != 0 else
                           'TOO_MANY' if len(lines) > MAX_INVENTORY_ENTRIES else 'OK')
    if snap['shared.refs'] == 'OK':
        for line in lines:
            if ' ' in line:
                ref, obj = line.split(' ', 1)
                snap['ref:' + ref] = obj
    snap['common.config'] = file_digest(os.path.join(common_dir, 'config'))
    hooks = os.path.join(common_dir, 'hooks')
    names = _listing(hooks)
    snap['common.hooks'] = 'TOO_MANY' if names is None else _sha(
        _canonical({n: file_digest(os.path.join(hooks, n)) for n in names}))
    trees = os.path.join(common_dir, 'worktrees')
    names = _listing(trees)
    if names is None:
        snap['worktree:'] = 'TOO_MANY'
    for name in names or []:
        files = ('gitdir', 'locked', 'commondir') if name == own_worktree else (
            'gitdir', 'locked', 'commondir', 'HEAD', 'index')
        for item in files:
            digest = file_digest(os.path.join(trees, name, item))
            # An unlocked worktree has no 'locked' file; that absence is a valid state.
            snap['worktree:%s/%s' % (name, item)] = 'ABSENT' if item == 'locked' and digest == 'MISSING' else digest
    return snap


def _failed(value):
    return not isinstance(value, str) or value in FAILURE_WORDS or value.startswith('GIT_EXIT')


def compare_inventory(pre, post, allowed_refs=()):
    """Internal drift and unverified labels. Allowed refs are the task's own branch refs."""
    labels = set(pre) | set(post)
    drift = sorted(label for label in labels if pre.get(label) != post.get(label)
                   and not (label.startswith('ref:') and label[4:] in allowed_refs))
    unverified = sorted(label for label in labels if any(_failed(s[label]) for s in (pre, post) if label in s))
    return {'drift': drift, 'unverified': unverified}


# --------------------------------------------------------------------- CLI
def _policy(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        data = json.loads(_read_limited(fd, MAX_POLICY_BYTES).decode('utf-8'))
    finally:
        os.close(fd)
    if not isinstance(data, dict):
        raise AuditError('audit policy is malformed')
    lists = [data.get(name, []) for name in ('protected_roots', 'allowed_paths', 'pinned_paths')]
    if not all(isinstance(value, list) for value in lists):
        raise AuditError('audit policy is malformed')
    policy = AuditPolicy(data.get('codex_root'), data.get('home'), tuple(lists[0]), tuple(lists[1]),
                         tuple(lists[2]), data.get('deny_verified', False), data.get('coverage_verified', False))
    _check_policy(policy)
    return policy


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
