"""Decide from verifiable evidence whether an old PM session has stopped (#683).

Fail closed: STOPPED needs every check to have run and passed. A failed check is
never read as "nothing there", and an absent TUI or closed window is no evidence.
Nor is finding nothing about an ID: a Claude session also needs a valid binding or
its own transcript, else it stays UNKNOWN.
ACTIVE evidence outranks UNKNOWN because both refuse a transfer and ACTIVE is the
more specific answer. The check never starts, stops or resumes a session; it only
runs ps, sends signal 0 and briefly takes the session lock.
"""

import json
import os
import shlex
import stat
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import active_pm_inbox, pm_runtime, role_config
from .errors import LLMToolError
from .pm_runtime_codex import _THREAD_ID

ACTIVE = 'ACTIVE'
STOPPED = 'STOPPED'
UNKNOWN = 'UNKNOWN'

PROCESS_CLEAN = 'PROCESS_CLEAN'
PROCESS_MATCH = 'PROCESS_MATCH'
PROCESS_LIST_FAILED = 'PROCESS_LIST_FAILED'
LOCK_FREE = 'LOCK_FREE'
LOCK_BUSY = 'LOCK_BUSY'
LOCK_CHECK_FAILED = 'LOCK_CHECK_FAILED'
REGISTRY_CLEAN = 'REGISTRY_CLEAN'
REGISTRY_LIVE_MATCH = 'REGISTRY_LIVE_MATCH'
REGISTRY_UNREADABLE = 'REGISTRY_UNREADABLE'
TRANSCRIPT_RECENT = 'TRANSCRIPT_RECENT'
TRANSCRIPT_QUIET = 'TRANSCRIPT_QUIET'
TRANSCRIPT_UNREADABLE = 'TRANSCRIPT_UNREADABLE'
TRANSCRIPT_WITHOUT_REGISTRY = 'TRANSCRIPT_WITHOUT_REGISTRY'
NO_CLAUDE_EVIDENCE = 'NO_CLAUDE_EVIDENCE'
CLAUDE_CONFIG_UNAVAILABLE = 'CLAUDE_CONFIG_UNAVAILABLE'
BINDING_INVALID = 'BINDING_INVALID'
CODEX_UNBOUND = 'CODEX_UNBOUND'
CODEX_STATE_INVALID = 'CODEX_STATE_INVALID'
SESSION_ID_INVALID = 'SESSION_ID_INVALID'
RUNTIME_UNSUPPORTED = 'RUNTIME_UNSUPPORTED'
CWD_INVALID = 'CWD_INVALID'
CHECK_FAILED = 'CHECK_FAILED'

_ACTIVE_EVIDENCE = frozenset((PROCESS_MATCH, LOCK_BUSY, REGISTRY_LIVE_MATCH, TRANSCRIPT_RECENT))
# Reported, but neither passes nor fails a check.
_INFORMATIONAL_EVIDENCE = frozenset((TRANSCRIPT_QUIET,))
# -ww: a line cut at the terminal width could hide the ID and read as a clean listing.
_PS_COMMAND = ('ps', '-axo', 'pid=,command=', '-ww')
_PS_TIMEOUT_SECONDS = 2
_MAX_REGISTRY_BYTES = 65536
_MAX_PID = 2 ** 31 - 1


def check_session(native_id, runtime, cwd, *, state_root=None, environ=None, home=None,
                  clock=None, process_lister=None, lock_held=False, self_pids=None):
    """Return {'state': ACTIVE|STOPPED|UNKNOWN, 'evidence': [fixed codes]}.

    The lock probe opens a new descriptor, so it reads as busy under the caller's
    own transfer_lock. With lock_held the caller holds that lock: the probe is
    skipped, and LOCK_FREE is recorded and required, bound session or not.
    """
    try:
        evidence, required = _collect(native_id, runtime, cwd, state_root, environ, home,
                                      clock, process_lister, lock_held, self_pids=self_pids)
    except Exception:
        evidence, required = [CHECK_FAILED], None
    evidence = list(dict.fromkeys(evidence))
    found = set(evidence)
    if found & _ACTIVE_EVIDENCE:
        state = ACTIVE
    elif found - _INFORMATIONAL_EVIDENCE == required:
        state = STOPPED
    else:
        state = UNKNOWN
    return {'state': state, 'evidence': evidence}


def _guard(failure_code, check, *args):
    try:
        return check(*args)
    except Exception:
        return [failure_code]


def _exists(path):
    # os.path.lexists reports False for every OSError, including a denied lookup.
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    return True


def _collect(native_id, runtime, cwd, state_root, environ, home, clock, process_lister,
             lock_held, self_pids=None):
    """(evidence, the exact evidence that STOPPED requires, or None if it cannot apply)."""
    if (type(native_id) is not str or
            active_pm_inbox.PM_NATIVE_SESSION_ID.fullmatch(native_id) is None):
        return [SESSION_ID_INVALID], None
    if type(runtime) is not str or runtime not in pm_runtime._SUPPORTED_BINDING_RUNTIMES:
        return [RUNTIME_UNSUPPORTED], None
    if (type(cwd) is not str or not cwd or len(cwd) > 1024 or
            any(ord(char) < 0x20 or ord(char) == 0x7f for char in cwd)):
        return [CWD_INVALID], None
    cwd = os.path.normpath(os.path.abspath(cwd))
    env = os.environ if environ is None else environ
    if home is None:
        home = env.get('HOME') or str(Path.home())
    evidence = []
    root = binding = None
    try:
        root = pm_runtime._session_state_root(
            state_root or Path(home) / '.local/state/llmtool/pm-runtime')
        binding = _load_binding(root, native_id, runtime, cwd)
    except Exception:
        evidence.append(BINDING_INVALID)
    ids = {native_id}
    if runtime == 'codex' and binding is not None:
        try:
            thread_id = _codex_thread_id(root, native_id, binding)
            if thread_id is not None:
                ids.add(thread_id)
        except Exception:
            evidence.append(CODEX_STATE_INVALID)
    evidence.extend(_guard(PROCESS_LIST_FAILED, _process_evidence, ids, process_lister,
                           self_pids))
    if lock_held:
        evidence.append(LOCK_FREE)
    elif binding is not None:
        evidence.extend(_guard(LOCK_CHECK_FAILED, _lock_evidence, native_id, root))
    if runtime == 'codex':
        if binding is None:
            evidence.append(CODEX_UNBOUND)
        return evidence, {PROCESS_CLEAN, LOCK_FREE}
    evidence.extend(_guard(CLAUDE_CONFIG_UNAVAILABLE, _claude_evidence, native_id, cwd,
                           binding, env, home, clock))
    required = {PROCESS_CLEAN, REGISTRY_CLEAN}
    return evidence, required | {LOCK_FREE} if lock_held or binding is not None else required


def _load_binding(root, native_id, runtime, cwd):
    """The session's dedicated binding, None when it has none; raise when unusable."""
    if not _exists(pm_runtime._binding_path(root, native_id)):
        return None
    binding = pm_runtime.current_binding(native_id, state_root=root,
                                         resolve_legacy_account=False)
    if binding is None or binding.get('runtime') != runtime or binding.get('cwd') != cwd:
        raise ValueError('binding does not describe this session')
    return binding


def _codex_thread_id(root, native_id, binding):
    path = pm_runtime._runtime_state_path(root, active_pm_inbox.pm_session_key(native_id))
    state = pm_runtime._read_state_json(root, path) if _exists(path) else {}
    if type(state) is not dict:
        raise ValueError('Codex runtime state is malformed')
    thread_id = state.get('codex_thread_id')
    if thread_id is None:
        if binding.get('session_started'):
            raise ValueError('a started Codex session has no recorded thread')
        return None
    if type(thread_id) is not str or _THREAD_ID.fullmatch(thread_id) is None:
        raise ValueError('Codex thread ID is malformed')
    return thread_id


def _list_processes():
    return subprocess.check_output(_PS_COMMAND, stdin=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=_PS_TIMEOUT_SECONDS)


def _process_evidence(ids, process_lister, self_pids=None):
    if self_pids is None:
        self_pids = {os.getpid(), os.getppid()}
    listing = (process_lister or _list_processes)()
    if isinstance(listing, (bytes, bytearray)):
        listing = bytes(listing).decode('utf-8', 'replace')
    if type(listing) is not str or not listing.strip():
        return [PROCESS_LIST_FAILED]
    parsed = False
    for line in listing.splitlines():
        fields = line.split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            if any(value in line for value in ids):
                return [PROCESS_MATCH]
            continue
        parsed = True
        pid = int(fields[0])
        if pid in self_pids or fields[0] in self_pids:
            continue
        # ps prints argv joined by spaces with its quoting lost, so a stray quote can
        # make shlex merge items; the plain split keeps every space-separated item.
        items = fields[1].split()
        try:
            items.extend(shlex.split(fields[1]))
        except ValueError:
            if any(value in line for value in ids):
                return [PROCESS_MATCH]
        if any(item in ids or item.partition('=')[2] in ids for item in items):
            return [PROCESS_MATCH]
    return [PROCESS_CLEAN] if parsed else [PROCESS_LIST_FAILED]


def _lock_evidence(native_id, root):
    try:
        with pm_runtime.transfer_lock(native_id, root):
            return [LOCK_FREE]
    except LLMToolError as exc:
        if getattr(exc, 'pm_runtime_code', None) == 'PM_SESSION_BUSY':
            return [LOCK_BUSY]
        raise


def _claude_config_dirs(binding, env, home):
    """Every Claude config directory this session could have run under."""
    from .config import _read_yaml, user_config_path

    path = user_config_path(home)
    user = _read_yaml(path, 'user config') if _exists(path) else {}
    accounts, _ = role_config.validate_role_config(user)
    account = None if binding is None else binding.get('account')
    if account is not None and accounts.get(account, {}).get('kind') != 'claude':
        raise ValueError('binding account is not a configured Claude account')
    candidates = [item['config_dir'] for item in accounts.values() if item['kind'] == 'claude']
    # Beyond the accounts, the two places a session runs without one: the legacy PM
    # setting and the CLI's own default. A session there would otherwise go unseen.
    legacy = user.get('pm_runtime', {}).get('claude_config_dir')
    if legacy is not None:
        candidates.append(legacy)
    if env.get('CLAUDE_CONFIG_DIR'):
        candidates.append(env['CLAUDE_CONFIG_DIR'])
    candidates.append(os.path.join(home, '.claude'))
    for candidate in candidates:
        if (type(candidate) is not str or not os.path.isabs(candidate) or
                any(ord(char) < 0x20 or ord(char) == 0x7f for char in candidate)):
            raise ValueError('Claude config directory is invalid')
    return list(dict.fromkeys(os.path.normpath(candidate) for candidate in candidates))


def _claude_evidence(native_id, cwd, binding, env, home, clock):
    value = (clock or time.time)()
    now = value.timestamp() if isinstance(value, datetime) else float(value)
    evidence = []
    # A clean registry holds just as well for an ID that never ran, so only a valid
    # binding or this session's own transcript shows the check is about a real session.
    observed = binding is not None
    for config_dir in _claude_config_dirs(binding, env, home):
        registry = _guard(REGISTRY_UNREADABLE, _registry_evidence, native_id, config_dir)
        transcript = _guard(TRANSCRIPT_UNREADABLE, _transcript_evidence, native_id, cwd,
                            config_dir, now)
        evidence.extend(registry + transcript)
        if transcript:
            observed = True
            if not registry:
                evidence.append(TRANSCRIPT_WITHOUT_REGISTRY)
    if not observed or not evidence:
        evidence.append(NO_CLAUDE_EVIDENCE)
    return evidence


def _transcript_evidence(native_id, cwd, config_dir, now):
    """[] when this directory has no transcript for the session."""
    env = {'CLAUDE_CONFIG_DIR': config_dir}
    if not active_pm_inbox.claude_session_present(native_id, cwd, env):
        return []
    modified = active_pm_inbox.claude_session_file(native_id, cwd, env).lstat().st_mtime
    # A timestamp ahead of the clock is treated as recent, not as quiet.
    if now - modified <= pm_runtime._LIVE_WINDOW_SECONDS:
        return [TRANSCRIPT_RECENT]
    return [TRANSCRIPT_QUIET]


def _plain_user_directory(path):
    """False when absent; raise for anything but this user's real directory."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('not a plain user directory')
    return True


def _registry_evidence(native_id, config_dir):
    """[] when this directory has no sessions directory. Only *.json is ever opened."""
    sessions = os.path.join(config_dir, 'sessions')
    if not _plain_user_directory(config_dir) or not _plain_user_directory(sessions):
        return []
    evidence = []
    with os.scandir(sessions) as entries:
        for entry in entries:
            if entry.name.endswith('.json'):
                evidence.extend(_guard(REGISTRY_UNREADABLE, _registry_entry_evidence,
                                       entry.path, native_id))
    return evidence or [REGISTRY_CLEAN]


def _registry_entry_evidence(path, native_id):
    # O_NOFOLLOW refuses a symlink; O_NONBLOCK keeps a FIFO from hanging the check.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_size > _MAX_REGISTRY_BYTES):
            raise ValueError('registry entry is not a bounded user file')
        with os.fdopen(fd, 'rb') as stream:
            fd = -1
            entry = json.loads(stream.read(_MAX_REGISTRY_BYTES + 1))
    finally:
        if fd >= 0:
            os.close(fd)
    if type(entry) is not dict:
        raise ValueError('registry entry is malformed')
    session_id, pid = entry.get('sessionId'), entry.get('pid')
    # A pid of 0 or below would address a process group in os.kill.
    if type(session_id) is not str or type(pid) is not int or not 0 < pid <= _MAX_PID:
        raise ValueError('registry entry is malformed')
    return [REGISTRY_LIVE_MATCH] if session_id == native_id and _pid_live(pid) else []


def _pid_live(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
