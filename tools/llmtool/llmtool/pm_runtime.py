"""Bounded driver for an already-running Claude Code PM session."""

import fcntl
import atexit
import collections
import contextlib
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import active_pm_inbox, role_config
from .errors import LLMToolError
from .test_output_excerpts import _CLOUD_KEY, _emitted_sensitive

# The Claude Code permission mode of the dedicated PM process (shown in the TUI status).
PM_PERMISSION_MODE = 'auto'

MAX_MESSAGE_BYTES = 16384
MAX_STREAM_LINE_BYTES = 1024 * 1024
MAX_HISTORY_TURNS = 200
_ADAPTERS = {'claude-code': 'claude', 'codex': 'codex'}
_SUPPORTED_BINDING_RUNTIMES = frozenset(('claude-code', 'codex'))
_LIVE_WINDOW_SECONDS = 20
_STDERR_LIMIT = 65536
# Fixed tool set for the PM process (claude CLI 2.1.312: --strict-mcp-config uses only the
# servers in --mcp-config, so claude.ai connectors can no longer join mid-session and
# invalidate the prompt cache). Permission policy is unchanged (--permission-prompts none).
PM_MCP_CONFIG = '{"mcpServers":{}}'
# Owner #304: no one-line "what I am about to do" text when the PM uses no tool. Passed
# with --append-system-prompt on every launch; the CLI snapshots the system prompt on a
# conversation's first request, so an already started session picks it up after compaction.
PM_SESSION_INSTRUCTION = (
    'llmtool PM session rule: when you answer without calling any tool, reply with the '
    'answer itself and nothing before it; never open with a one-line preamble about what '
    'you are about to do. A short preamble is fine only in turns where you call tools.')
# Stream-json lines that only a turn produces. Anything else that arrives between turns
# (system notices, a late control_response) is not the start of a Claude Code turn.
_TURN_LINE_TYPES = ('stream_event', 'assistant', 'user', 'result')
_STDOUT_EOF = object()
_UNSET = object()
# Claude Code 2.1.312 reports the fate of a user message sent with a uuid as
# `command_lifecycle` frames: queued, started (it drains into a turn), then one of
# completed / cancelled / discarded / refused.
_NOT_ACCEPTED = ('cancelled', 'discarded', 'refused')
_TIMED_OUT = object()
_LIFECYCLE_STATES = ('queued', 'started', 'completed') + _NOT_ACCEPTED
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
_now = time.monotonic
# Bounds for correlating an Owner message (measured on claude 2.1.312 against a local fake
# Messages API: `queued` arrived 1-20 ms after the write, also while a turn was running;
# `started` arrived 10-20 ms after `queued` when idle, or 0-20 ms after the result of the
# turn Claude Code ran first; `completed` 0-10 ms after the turn's result). Past a bound the
# message's fate is UNKNOWN: it is reported as such and never sent again automatically.
PM_QUEUED_TIMEOUT = 10.0   # write -> `queued`, absolute (a running turn does not extend it)
PM_STARTED_GRACE = 10.0    # `queued` -> `started`, counted only while no turn is running
PM_TERMINAL_GRACE = 10.0   # the turn's result -> `completed` (not a measured guarantee)
PM_CANCEL_GRACE = 10.0     # Esc before `started` -> `cancelled` or `started`

# Owner-message lifecycle (High Debug hd665, RCA_CONFIRMED). Live phases: W written,
# awaiting `queued`; Q queued, awaiting `started`; O started as its own turn; A started
# inside a turn already streaming (absorption candidate); RO / RA that turn's result seen,
# awaiting `completed`. Terminal: C completed, B absorbed, N not sent (withdrawn), X
# unknown. Every frame of the turn goes through _owner_transition; every transition not
# listed is illegal and ends X (held for the Owner, never replayed).
_LIVE = ('W', 'Q', 'O', 'A', 'RO', 'RA')
_TERMINAL = ('C', 'B', 'N', 'X')
_OWNER_EVENTS = ('queued', 'started', 'completed') + _NOT_ACCEPTED + (
    'output', 'result', 'other_started', 'notice', 'timeout', 'esc', 'exit', 'invalid')
_TRANSITIONS = {(phase, event): ('X', 'hold_unknown')
                for phase in _LIVE for event in _OWNER_EVENTS}
for _phase in _LIVE:
    _TRANSITIONS[_phase, 'notice'] = (_phase, 'ignore')
    _TRANSITIONS[_phase, 'esc'] = (_phase, 'pause_and_interrupt')
_TRANSITIONS['W', 'queued'] = ('Q', 'acknowledge')
_TRANSITIONS['Q', 'started'] = ('START', 'accept')
for _event in _NOT_ACCEPTED:
    _TRANSITIONS['Q', _event] = ('N', 'retain_rejection_reason')
for _phase in ('W', 'Q'):
    _TRANSITIONS[_phase, 'output'] = (_phase, 'background_output')
    _TRANSITIONS[_phase, 'result'] = (_phase, 'background_result')
    _TRANSITIONS[_phase, 'other_started'] = (_phase, 'background_start')
for _phase in ('O', 'A'):
    _TRANSITIONS[_phase, 'output'] = (_phase, 'render_output')
_TRANSITIONS['O', 'result'] = ('RO', 'save_result')
_TRANSITIONS['A', 'result'] = ('RA', 'save_result')
_TRANSITIONS['RO', 'completed'] = ('C', 'finish_owner')
_TRANSITIONS['RA', 'completed'] = ('B', 'finish_absorbed')
del _phase, _event
_DELIVERY = {'C': 'COMPLETED', 'B': 'ABSORBED', 'N': 'NOT_SENT', 'X': 'UNKNOWN'}


def _owner_transition(phase, event, background_active):
    if phase in _TERMINAL:
        return phase, 'route_late_without_replay'
    next_phase, action = _TRANSITIONS[phase, event]
    if next_phase == 'START':
        next_phase = 'A' if background_active else 'O'
    return next_phase, action


class _Line(bytes):
    """A stdout line stamped with its arrival time when the reader took it."""
_active_runtimes = set()
_signal_handlers_installed = False
_previous_signal_handlers = {}
_atexit_registered = False


def _error(code, message):
    exc = LLMToolError(message)
    exc.pm_runtime_code = code
    return exc


def _timestamp(clock):
    value = clock()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


def _ensure_state_dir(path):
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise _error('PM_STATE_INVALID', 'PM runtime state path must be absolute')
    # Create the requested directory without following a symlink at its leaf.
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
    except OSError:
        raise _error('PM_STATE_INVALID', 'PM runtime state directory is unavailable') from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise _error('PM_STATE_INVALID', 'PM runtime state directory is not a user directory')
    os.chmod(path, 0o700)
    return path


def _check_state_file(path, *, create=False):
    if not hasattr(os, 'O_NOFOLLOW'):
        raise _error('PM_STATE_INVALID', 'no-follow state file access is unavailable')
    try:
        prior = path.lstat()
        if not stat.S_ISREG(prior.st_mode) or prior.st_uid != os.getuid():
            raise _error('PM_STATE_INVALID', 'PM runtime state file is not a user file')
    except FileNotFoundError:
        if not create:
            raise _error('PM_STATE_INVALID', 'PM runtime state file is unavailable') from None
    except OSError:
        raise _error('PM_STATE_INVALID', 'PM runtime state file is unavailable') from None
    flags = os.O_RDWR
    if create:
        flags |= os.O_CREAT
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags, 0o600)
        info = os.fstat(fd)
    except OSError:
        raise _error('PM_STATE_INVALID', 'PM runtime state file is unavailable') from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        os.close(fd)
        raise _error('PM_STATE_INVALID', 'PM runtime state file is not a user file')
    os.fchmod(fd, 0o600)
    return fd


def _contains_different_session(value, expected):
    if isinstance(value, dict):
        sid = value.get('session_id')
        if sid is not None and sid != expected:
            return True
        return any(_contains_different_session(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_contains_different_session(child, expected) for child in value)
    return False


def _iter_content(content):
    if isinstance(content, list):
        yield from content


def _safe_result_text(value):
    if not isinstance(value, str):
        return ''
    try:
        if len(value.encode('utf-8')) > MAX_MESSAGE_BYTES or _emitted_sensitive(value):
            return '[withheld]'
    except UnicodeError:
        return '[withheld]'
    return value


def _screen_stream_text(value):
    """Redact credential-like text before it crosses the runtime event boundary."""
    from .task_submission_cli import _redact_rejected_output
    screened = _redact_rejected_output(value)
    if screened.startswith('[REDACTED_SENSITIVE_OUTPUT]') or _emitted_sensitive(screened):
        return '[withheld]'
    return screened


# W57: what a tool call does, for the TUI line. Bash's own description, else the tool name
# with its path or pattern; never a command or any other input field.
_TOOL_TARGET_KEYS = ('pattern', 'file_path', 'notebook_path', 'path', 'url')
TOOL_DESCRIPTION_CHARS = 240


def _display_local_path(value, cwd=None):
    """Shorten paths inside the repository or home; keep other paths intact."""
    if not isinstance(value, str) or not value.startswith('/'):
        return value
    path = os.path.normpath(value)
    roots = []
    if isinstance(cwd, str) and cwd:
        roots.append((os.path.normpath(os.path.abspath(cwd)), 'repo'))
    home = os.path.normpath(str(Path.home()))
    roots.append((home, 'home'))
    for root, kind in roots:
        try:
            if os.path.commonpath((root, path)) == root:
                relative = os.path.relpath(path, root)
                if kind == 'repo':
                    return '.' if relative == '.' else relative
                return '~' if relative == '.' else '~/' + relative
        except ValueError:
            continue
    return path


def _is_display_path(value):
    if not isinstance(value, str) or not value:
        return False
    if value in ('.', '~', '[root]'):
        return True
    tail = value[2:] if value.startswith('~/') else value[1:] if value.startswith('/') else value
    return all(
        part
        and part not in ('.', '..')
        and part == part.strip()
        and all(char.isprintable() for char in part)
        for part in tail.split('/')
    )


def display_path(value, cwd=None):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value == '/':
        return '[root]'
    raw = value[1:] if value.startswith('/') else value
    if not _is_display_path(raw):
        return None
    shown = _display_local_path(value, cwd)
    return shown if _is_display_path(shown) else None


def _tool_description(name, tool_input, cwd=None):
    if (not isinstance(tool_input, dict)
            or not isinstance(name, str)
            or '/' in name
            or not name.isprintable()):
        return None
    raw = tool_input.get('description')
    text = None
    if isinstance(raw, str):
        text = ' '.join(''.join(char for char in raw
                                if char.isprintable() or char.isspace()).split())

    if not text:
        for key in _TOOL_TARGET_KEYS:
            target = display_path(tool_input.get(key), cwd)
            if target:
                text = name + ' ' + target
                break

    if not text:
        return None
    screened = _screen_stream_text(text[:TOOL_DESCRIPTION_CHARS])
    return screened if screened != '[withheld]' else None


_STREAM_SECRET_START = re.compile(
    r'-----BEGIN(?: [A-Z]+)* PRIVATE KEY-----|'
    + _CLOUD_KEY + r'|'
    r'(?<!(?-i:[A-Za-z0-9_]))(?:AKIA|ASIA)[A-Z0-9]*|\bsk-[A-Za-z0-9_-]*|'
    r'\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]*|'
    r'\bxox[abprs]-[A-Za-z0-9-]*|\b(?:bearer|basic)\s+\S*|'
    r'\beyJ[A-Za-z0-9_.-]*', re.IGNORECASE)
_STREAM_URL_SCHEME = re.compile(r'[a-z][a-z0-9+.-]*', re.IGNORECASE)


def _stream_safe_end(value):
    """Return the prefix boundary before any trailing, unfinished secret token."""
    safe_end = len(value)
    for match in _STREAM_SECRET_START.finditer(value):
        tail = value[match.start():]
        # Complete matches have already been redacted by _screen_stream_text.
        # Keep an unredacted candidate only while it can still grow into a token.
        if ('[REDACTED]' not in tail and '[REDACTED_SENSITIVE_OUTPUT]' not in tail and
                not any(char.isspace() for char in tail)):
            safe_end = min(safe_end, match.start())
        elif match.group(0).startswith('-----BEGIN') and '-----END' not in tail:
            safe_end = min(safe_end, match.start())
    # A delta can end in the middle of a marker (for example "gh" + "p_").
    # Hold only that partial marker so the next delta can be screened as a whole.
    markers = ('-----BEGIN', 'github_pat_', 'AKIA', 'ASIA', 'sk-', 'ghp_',
               'gho_', 'ghu_', 'ghs_', 'ghr_', 'xox', 'eyJ', 'bearer', 'basic')
    lowered = value.lower()
    for marker in markers:
        marker = marker.lower()
        for size in range(1, len(marker)):
            start = len(value) - size
            if lowered.endswith(marker[:size]) and (
                    start == 0 or not (
                        value[start - 1].isascii()
                        and (value[start - 1].isalnum() or value[start - 1] == '_'))):
                safe_end = min(safe_end, start)
    token_start = max((index for index, char in enumerate(value) if char.isspace()),
                      default=-1) + 1
    tail = value[token_start:]
    for scheme in _STREAM_URL_SCHEME.finditer(tail):
        start, end = scheme.span()
        if ((start == 0 or not (tail[start - 1].isalnum() or tail[start - 1] == '_')) and
                tail.startswith('://', end)):
            authority = tail[end + 3:]
            authority_end = min((position for marker in '/?#'
                                 if (position := authority.find(marker)) >= 0),
                                default=len(authority))
            if ':' in authority[:authority_end]:
                safe_end = min(safe_end, token_start + start)
                break
    return safe_end


def _bounded_stream_append(current, value):
    """Keep persisted partial output within the public message size bound."""
    value = value.encode('utf-8', 'ignore').decode('utf-8')
    room = MAX_MESSAGE_BYTES - len(current.encode('utf-8'))
    if room <= 0:
        return current
    addition = value.encode('utf-8')[:room].decode('utf-8', 'ignore')
    return current + addition


def _history_path(root, key):
    return root / (key + '.history.json')


def _runtime_state_path(root, key):
    return root / (key + '.runtime.json')


def _binding_path(root, native_session_id):
    return root / (native_session_id + '.binding.json')


def _write_json_atomic(root, path, value):
    fd, temporary = tempfile.mkstemp(prefix='.pm-runtime-', dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            fd = -1
            json.dump(value, stream, ensure_ascii=True, sort_keys=True,
                      separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        raise _error('PM_BINDING_WRITE_FAILED', 'PM session binding could not be saved') from None
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def current_binding(pm_session_id=None, *, state_root=None, environ=None,
                    home=None, resolve_legacy_account=True, resolved_account=_UNSET):
    """Read a persisted dedicated-session binding, or the most recently created one."""
    root_arg = state_root or Path.home() / '.local/state/llmtool/pm-runtime'
    root = _ensure_state_dir(Path(root_arg))
    if pm_session_id is not None:
        native_id = pm_session_id
    else:
        pointer = root / 'current-binding.json'
        try:
            value = _read_state_json(root, pointer)
            native_id = value.get('native_session_id') if type(value) is dict else None
        except (LLMToolError, OSError, ValueError, AttributeError):
            return None
    if type(native_id) is not str or not active_pm_inbox.PM_NATIVE_SESSION_ID.fullmatch(native_id):
        return None
    path = _binding_path(root, native_id)
    try:
        binding = _read_state_json(root, path)
    except (LLMToolError, OSError, ValueError):
        return None
    expected = {'native_session_id': native_id, 'dedicated': True}
    allowed_fields = {'runtime', 'account', 'native_session_id', 'cwd', 'dedicated',
                      'created_at', 'session_started'}
    if (not isinstance(binding, dict) or set(binding) - allowed_fields or
            any(binding.get(k) != v for k, v in expected.items()) or
            not {'native_session_id', 'cwd', 'dedicated', 'created_at'} <= set(binding) or
            type(binding.get('dedicated')) is not bool or type(binding.get('cwd')) is not str or
            type(binding.get('created_at')) is not str or
            type(binding.get('session_started', False)) is not bool or
            binding.get('runtime', 'claude-code') not in _SUPPORTED_BINDING_RUNTIMES or
            ('account' in binding and binding['account'] is not None and
             not role_config._name(binding['account']))):
        return None
    binding.setdefault('runtime', 'claude-code')
    if 'account' not in binding:
        if resolve_legacy_account:
            if resolved_account is not _UNSET:
                binding['account'] = resolved_account
            else:
                try:
                    binding['account'] = role_config.resolve_role(
                        'pm', environ=environ,
                        home=(home if home is not None else
                              (None if environ is None else environ.get('HOME')))).get('account')
                except LLMToolError:
                    return None
    # Bindings written before session_started existed already went through the
    # old creator process, so they must be resumed rather than initialized again.
    binding.setdefault('session_started', True)
    return binding


def _read_state_json(root, path):
    """Read a regular file directly inside the PM state directory, without symlinks."""
    if path.parent != root or not hasattr(os, 'O_NOFOLLOW'):
        raise _error('PM_BINDING_INVALID', 'PM binding is not a safe state file')
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise _error('PM_BINDING_INVALID', 'PM binding is not a regular user file')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid():
            os.close(fd)
            raise _error('PM_BINDING_INVALID', 'PM binding is not a regular user file')
        with os.fdopen(fd, encoding='utf-8') as stream:
            return json.load(stream)
    except LLMToolError:
        raise
    except (OSError, ValueError, UnicodeError):
        raise _error('PM_BINDING_INVALID', 'PM binding is malformed or unavailable') from None


def list_bindings(*, state_root=None, environ=None, home=None):
    """Return valid dedicated PM bindings newest first; reject unsafe entries."""
    root_arg = state_root or Path.home() / '.local/state/llmtool/pm-runtime'
    root = _ensure_state_dir(Path(root_arg))
    bindings = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if not entry.name.endswith('.binding.json'):
                    continue
                native_id = entry.name[:-len('.binding.json')]
                if not active_pm_inbox.PM_NATIVE_SESSION_ID.fullmatch(native_id):
                    raise _error('PM_BINDING_INVALID', 'PM binding filename is malformed')
                valid = current_binding(native_id, state_root=root, environ=environ,
                                        home=home)
                if valid is None:
                    raise _error('PM_BINDING_INVALID', 'PM binding is malformed')
                bindings.append(valid)
    except OSError:
        raise _error('PM_BINDING_INVALID', 'PM binding directory is unavailable') from None
    return sorted(bindings, key=lambda item: (item['created_at'], item['native_session_id']),
                  reverse=True)


def select_binding(native_id, *, state_root=None):
    """Move the current PM binding pointer without opening or starting a session."""
    if type(native_id) is not str or not active_pm_inbox.PM_NATIVE_SESSION_ID.fullmatch(native_id):
        raise _error('PM_BINDING_INVALID', 'PM binding ID is invalid')
    root_arg = state_root or Path.home() / '.local/state/llmtool/pm-runtime'
    root = _ensure_state_dir(Path(root_arg))
    binding = current_binding(native_id, state_root=root)
    if binding is None:
        raise _error('PM_BINDING_INVALID', 'PM binding is malformed or unavailable')
    _write_json_atomic(root, root / 'current-binding.json', {'native_session_id': native_id})
    return binding


def create_dedicated_pm(cwd, runtime='claude-code', account=None, *, state_root=None,
                        clock=None, environ=None, home=None):
    """Reserve a fresh PM session ID and durably bind its runtime and account."""
    if type(runtime) is not str or runtime not in _SUPPORTED_BINDING_RUNTIMES:
        raise _error('PM_RUNTIME_UNSUPPORTED', 'PM runtime is unsupported')
    try:
        cwd = os.path.normpath(os.path.abspath(cwd))
        if not os.path.isdir(cwd):
            raise ValueError()
    except (OSError, TypeError, ValueError):
        raise _error('PM_SESSION_INVALID', 'PM working directory is invalid') from None
    native_id = str(uuid.uuid4())
    try:
        role_options = {'environ': environ, 'home': home}
        if account is not None:
            role_options['cli'] = {'account': account}
        # A Codex binding records its own account. Resolve that account directly
        # instead of inheriting roles.pm, which may intentionally remain Claude.
        role = 'pm-codex-binding' if runtime == 'codex' and account is not None else 'pm'
        resolved = role_config.resolve_role(role, **role_options)
        if (runtime == 'codex' and
                (resolved.get('provider') != 'codex' or
                 not role_config._name(resolved.get('account')) or
                 not resolved.get('config_dir'))):
            raise LLMToolError('Codex PM binding requires a Codex account')
    except LLMToolError:
        raise _error('PM_CONFIG_INVALID', 'PM account configuration is invalid') from None
    account = resolved.get('account')
    root_arg = state_root or Path.home() / '.local/state/llmtool/pm-runtime'
    root = _ensure_state_dir(Path(root_arg))
    binding = {'runtime': runtime, 'account': account,
               'native_session_id': native_id, 'cwd': cwd,
               'dedicated': True, 'created_at': _timestamp(clock or time.time),
               'session_started': False}
    _write_json_atomic(root, _binding_path(root, native_id), binding)
    _write_json_atomic(root, root / 'current-binding.json',
                       {'native_session_id': native_id})
    return binding


def _session_live_elsewhere(native_id, transcript, now, created_at=None,
                            *, ignore_recent_transcript=False):
    try:
        listing = subprocess.check_output(['ps', '-axo', 'pid=,command='],
                                          stderr=subprocess.DEVNULL, timeout=2)
        for line in listing.decode('utf-8', 'replace').splitlines():
            try:
                _, command = line.strip().split(None, 1)
                argv = shlex.split(command)
            except (ValueError, IndexError):
                continue
            if (argv and os.path.basename(argv[0]).lower() == 'claude' and
                    native_id in argv[1:]):
                return True
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        modified = transcript.stat().st_mtime
        is_newer_than_binding = created_at is None or modified > created_at
        if ignore_recent_transcript:
            return False
        return is_newer_than_binding and 0 <= now - modified <= _LIVE_WINDOW_SECONDS
    except OSError:
        return False


def _session_state_root(state_root):
    return _ensure_state_dir(
        Path(state_root or Path.home() / '.local/state/llmtool/pm-runtime'))


def _fence_path(root, key):
    return root / (key + '.fence.json')


@contextlib.contextmanager
def transfer_lock(native_id, state_root=None):
    """Hold the session's driver lock for the whole block, or raise PM_SESSION_BUSY."""
    key = active_pm_inbox.pm_session_key(native_id)
    root = _session_state_root(state_root)
    lock_fd = _check_state_file(root / (key + '.lock'), create=True)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _error('PM_SESSION_BUSY', 'PM session already has an active driver') from None
        except OSError:
            raise _error('PM_STATE_INVALID', 'PM session lock could not be acquired') from None
        yield
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def write_session_fence(native_id, new_session_key, at, state_root=None):
    """Durably record that this session was transferred, so it can never reconnect."""
    key = active_pm_inbox.pm_session_key(native_id)
    if (type(new_session_key) is not str or new_session_key == key or
            active_pm_inbox.SESSION_ID.fullmatch(new_session_key) is None or
            type(at) is not str or not at):
        raise _error('PM_SESSION_INVALID', 'PM session fence is invalid')
    root = _session_state_root(state_root)
    _write_json_atomic(root, _fence_path(root, key),
                       {'native_session_id': native_id, 'new_session_key': new_session_key,
                        'at': at})


def read_session_fence(native_id, state_root=None):
    """The session's fence; None only when no fence file exists.

    Any other non-None value means fenced: a fence that is malformed, unsafe or
    unreadable returns a marker instead of the recorded fields.
    """
    unreadable = {'fence_state': 'UNREADABLE'}
    try:
        root = _session_state_root(state_root)
        path = _fence_path(root, active_pm_inbox.pm_session_key(native_id))
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        value = _read_state_json(root, path)
    except (LLMToolError, OSError, ValueError):
        return unreadable
    if (type(value) is not dict or
            set(value) != {'native_session_id', 'new_session_key', 'at'} or
            value['native_session_id'] != native_id or
            type(value['new_session_key']) is not str or type(value['at']) is not str):
        return unreadable
    return value


def refuse_fenced_session(native_id, root):
    if read_session_fence(native_id, root) is not None:
        raise _error('PM_SESSION_TRANSFERRED', 'PM session was transferred and cannot reconnect')


def _terminate_all(signum=None, frame=None):
    previous = _previous_signal_handlers.get(signum, signal.SIG_DFL)
    if previous is None:
        previous = signal.SIG_DFL
    close_error = None
    for runtime in tuple(_active_runtimes):
        try:
            runtime.close()
        except Exception as exc:
            if close_error is None:
                close_error = exc
    if signum is None or previous is signal.SIG_IGN:
        if close_error is not None:
            raise close_error
        return
    if callable(previous):
        previous(signum, frame)
        if close_error is not None:
            raise close_error
        return
    # The old disposition is now restored by the final runtime close. Re-sending
    # the signal lets the operating system apply its original default action.
    os.kill(os.getpid(), signum)
    if close_error is not None:
        raise close_error


def _restore_signal_handlers():
    global _signal_handlers_installed
    for signum, handler in tuple(_previous_signal_handlers.items()):
        try:
            # Do not overwrite a handler installed by another owner while this
            # runtime was open. The cache can be stale if that owner replaced
            # our handler, so the live disposition is authoritative.
            if signal.getsignal(signum) is _terminate_all:
                signal.signal(signum, signal.SIG_DFL if handler is None else handler)
        except (ValueError, OSError):
            pass
    _previous_signal_handlers.clear()
    _signal_handlers_installed = False


def _unregister_lifecycle(runtime):
    _active_runtimes.discard(runtime)
    if not _active_runtimes:
        _restore_signal_handlers()


def _register_lifecycle(runtime):
    global _signal_handlers_installed, _atexit_registered
    _active_runtimes.add(runtime)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        try:
            current = signal.getsignal(signum)
            if current is not _terminate_all:
                _previous_signal_handlers[signum] = current
                signal.signal(signum, _terminate_all)
        except (ValueError, OSError):
            pass
    _signal_handlers_installed = any(
        signal.getsignal(signum) is _terminate_all
        for signum in (signal.SIGTERM, signal.SIGHUP))
    if _signal_handlers_installed and not _atexit_registered:
        atexit.register(_terminate_all)
        _atexit_registered = True


class _TurnParser:
    """One turn's stream-json lines -> runtime events, pushed one line at a time. With
    defer, the turn's result is kept as `outcome` instead of finishing the record: the
    Owner-message reducer finishes it only on a legal terminal lifecycle frame."""

    def __init__(self, runtime, record, interrupted, defer=False):
        self.runtime, self.record, self.interrupted, self.defer = runtime, record, interrupted, defer
        self.seq = 0
        self.blocks = {}
        self.withheld = set()
        self.emitted = {}
        self.outcome = None   # (status, result_text, usage) of a deferred result

    def emit(self, kind, **fields):
        value = {'type': kind, 'turn_id': self.record['turn_id'], 'seq': self.seq}
        if self.record.get('origin'):
            value['origin'] = self.record['origin']
        self.seq += 1
        value.update(fields)
        return value

    def _flush_block(self, index, force=False):
        if index in self.withheld:
            return None
        buffered = self.blocks.get(index, '')
        screened = _screen_stream_text(buffered)
        if screened == '[withheld]':
            self.withheld.add(index)
            return None
        previous = self.emitted.get(index, 0)
        safe_end = len(screened) if force else _stream_safe_end(screened)
        safe_end = max(previous, safe_end)
        if safe_end <= previous:
            return None
        piece = screened[previous:safe_end]
        self.emitted[index] = safe_end
        self.record['result_text'] = _bounded_stream_append(
            self.record.get('result_text', ''), piece)
        return self.emit('TEXT_DELTA', text=piece, block_index=index)

    def flush_all(self):
        out = []
        for block_index in self.blocks:
            event = self._flush_block(block_index, force=True)
            if event is not None:
                out.append(event)
        return out

    def _invalid(self, out):
        out.append(self.emit('RUNTIME_ERROR', code='PM_STREAM_INVALID'))
        self.runtime._terminate_child()
        self.runtime._finish(self.record, 'ERROR')
        out.append(self.emit('TURN_END', status='ERROR', result_text='', usage={}))
        return out, True

    def feed(self, raw):
        """(events, done) for one stdout line; done once the turn's result was read."""
        runtime, record = self.runtime, self.record
        out = self.flush_all() if self.interrupted() else []
        if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_STREAM_LINE_BYTES:
            return self._invalid(out)
        try:
            line = json.loads(bytes(raw).decode('utf-8'))
        except (ValueError, UnicodeError):
            line = None
        if not isinstance(line, dict):
            return self._invalid(out)
        if _contains_different_session(line, runtime.pm_session_id):
            fork_error = _error('PM_SESSION_FORKED', 'Claude Code started a different PM session')
            try:
                runtime._finish(record, 'ERROR')
            except Exception:
                with runtime._state_lock:
                    if runtime._turn is record:
                        runtime._turn = None
            try:
                runtime.close()
            except Exception:
                pass
            raise fork_error
        kind = line.get('type')
        if kind == 'stream_event':
            event = line.get('event') or {}
            delta = event.get('delta') or {}
            if event.get('type') == 'message_start':
                self.blocks.clear()
                self.withheld.clear()
                self.emitted.clear()
            elif event.get('type') == 'content_block_delta' and delta.get('type') == 'text_delta':
                text = delta.get('text')
                if isinstance(text, str):
                    index = event.get('index', 0)
                    if index in self.withheld:
                        return out, False
                    self.blocks[index] = self.blocks.get(index, '') + text
                    buffered = self.blocks[index]
                    if len(buffered.encode('utf-8', 'ignore')) > MAX_MESSAGE_BYTES:
                        self.withheld.add(index)
                    else:
                        screened = _screen_stream_text(buffered)
                        if screened == '[withheld]':
                            self.withheld.add(index)
                        else:
                            flushed = self._flush_block(index)
                            if flushed is not None:
                                out.append(flushed)
            elif event.get('type') == 'content_block_stop':
                flushed = self._flush_block(event.get('index', 0), force=True)
                if flushed is not None:
                    out.append(flushed)
            elif event.get('type') == 'content_block_start':
                # Thinking never becomes answer text (only text_delta and text items
                # do); the UI may show a status while the PM thinks.
                block = event.get('content_block') or {}
                if isinstance(block, dict) and block.get('type') in ('thinking', 'redacted_thinking'):
                    out.append(self.emit('THINKING'))
        elif kind == 'assistant':
            for position, item in enumerate(
                    _iter_content((line.get('message') or {}).get('content'))):
                if not isinstance(item, dict):
                    continue
                if item.get('type') == 'text' and isinstance(item.get('text'), str):
                    index = item.get('index', position)
                    if index in self.withheld:
                        continue
                    final_text = _screen_stream_text(item['text'])
                    if final_text == '[withheld]':
                        self.withheld.add(index)
                        continue
                    flushed = self._flush_block(index, force=True)
                    if flushed is not None:
                        out.append(flushed)
                    previous = self.emitted.get(index, 0)
                    remainder = final_text[min(previous, len(final_text)):]
                    record['result_text'] = _bounded_stream_append(
                        record.get('result_text', ''), remainder)
                    self.blocks[index] = final_text
                    self.emitted[index] = len(final_text)
                    out.append(self.emit('ASSISTANT_TEXT', text=final_text, final=True))
                elif item.get('type') == 'tool_use':
                    tool_id, name = item.get('id'), item.get('name')
                    if isinstance(name, str):
                        if name not in record['tool_names']:
                            record['tool_names'].append(name)
                        out.append(self.emit('TOOL_USE', tool_use_id=tool_id, name=name,
                                             description=_tool_description(
                                                 name, item.get('input'), self.runtime.cwd)))
        elif kind == 'user':
            for item in _iter_content((line.get('message') or {}).get('content')):
                if isinstance(item, dict) and item.get('type') == 'tool_result':
                    is_error = bool(item.get('is_error', False))
                    permission = (item.get('permission_denial') or item.get('permission') or
                                  item.get('permission_denied'))
                    content = item.get('content', '')
                    permission_text = content.lower() if isinstance(content, str) else ''
                    denied = bool(permission) or (is_error and any(
                        word in permission_text for word in ('permission denied',
                                                             "doesn't want to proceed",
                                                             'denied by user')))
                    reason = ('PERMISSION_DENIED' if denied else
                              'TOOL_ERROR' if is_error else 'OK')
                    out.append(self.emit('TOOL_RESULT', tool_use_id=item.get('tool_use_id'),
                                         reason=reason, is_error=is_error))
        elif kind == 'result':
            status = 'INTERRUPTED' if self.interrupted() else (
                'ERROR' if line.get('is_error') or line.get('subtype') in
                ('error', 'error_during_execution') else 'COMPLETED')
            result = line.get('result', '')
            result_text = _screen_stream_text(result) if isinstance(result, str) else ''
            if result_text == '[withheld]':
                result_text = ''
            # The turn result closes any remaining text blocks.
            out.extend(self.flush_all())
            if not result_text:
                result_text = record.get('result_text', '')
            usage = line.get('usage') if isinstance(line.get('usage'), dict) else {}
            if status == 'ERROR':
                lower = result_text.lower()
                code = ('PM_QUOTA_EXHAUSTED' if any(word in lower for word in
                        ('quota exceeded', 'usage limit', 'rate limit', 'hit your limit'))
                        else 'PM_PROCESS_EXITED')
                out.append(self.emit('RUNTIME_ERROR', code=code, stderr=runtime._stderr_excerpt()))
            if self.defer:
                self.outcome = (status, result_text, usage)
                return out, True
            runtime._finish(record, status, result_text, usage)
            out.append(self.emit('TURN_END', status=status,
                                 result_text=_safe_result_text(result_text), usage=usage))
            return out, True
        else:
            runtime._unknown_lines += 1
        return out, False

    def exit_error(self):
        """The RUNTIME_ERROR for a child whose stdout ended inside a turn."""
        runtime = self.runtime
        if runtime._stderr_thread is not None:
            runtime._stderr_thread.join(timeout=1)
        stderr = runtime._stderr_excerpt()
        lower = stderr.lower()
        code = ('PM_QUOTA_EXHAUSTED' if any(word in lower for word in
                ('quota exceeded', 'usage limit', 'rate limit')) else 'PM_PROCESS_EXITED')
        return self.emit('RUNTIME_ERROR', code=code, stderr=stderr)

    def eof(self):
        if self.record.get('status') != 'ACTIVE':   # stdout ended inside the turn
            return []
        out = [self.exit_error()]
        self.runtime._finish(self.record, 'ERROR')
        out.append(self.emit('TURN_END', status='ERROR', result_text='', usage={}))
        return out


class PMRuntime:
    """Drive exactly one bound Claude PM session at a time."""

    def __init__(self, pm_session_id, *, cwd, runtime='claude-code', account=None, runner=None,
                 state_root=None, environ=None, clock=None):
        self.pm_session_id = pm_session_id
        self.cwd = cwd
        self.runtime = runtime
        self.account = account
        self.runner = runner
        self.state_root = state_root
        self.environ = environ
        self.clock = clock or time.time
        self.claude_config_dir = None  # The Claude account directory the PM runs under.
        self._key = None
        self._lock_fd = None
        self._child = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._turn = None
        self._closed = False
        self._has_owned_session = False
        self._was_interrupted = False
        self._unknown_lines = 0
        self._codex_driver = None
        self._stderr_buffer = bytearray()
        self._stderr_thread = None
        # The child's stdout is read for the whole connection, not only during an Owner
        # turn: Claude Code also starts turns by itself (background task notifications).
        self._lines = None
        self._lines_cond = threading.Condition(self._state_lock)
        self._stdout_thread = None
        self._idempotency = {}
        self._attempt = None              # the live Owner-message attempt, if any
        self._finished_attempts = collections.OrderedDict()   # uuid -> terminal phase
        self._protocol_alerts = 0         # lifecycle frames after a latched receipt

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def _history_read(self):
        path = _history_path(self._root, self._key)
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except FileNotFoundError:
            return []
        except OSError:
            raise _error('PM_HISTORY_INVALID', 'PM runtime history is unavailable') from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise _error('PM_HISTORY_INVALID', 'PM runtime history is not a user file')
            with os.fdopen(fd, 'r', encoding='utf-8') as stream:
                fd = -1
                data = json.load(stream)
            if (not isinstance(data, dict) or data.get('pm_session_id') != self.pm_session_id or
                    not isinstance(data.get('turns'), list)):
                raise _error('PM_HISTORY_INVALID', 'PM runtime history is malformed')
            return data['turns']
        except (ValueError, UnicodeError, OSError):
            raise _error('PM_HISTORY_INVALID', 'PM runtime history is malformed') from None
        finally:
            if fd >= 0:
                os.close(fd)

    def _history_write(self, turns):
        path = _history_path(self._root, self._key)
        if not hasattr(os, 'O_NOFOLLOW'):
            raise _error('PM_HISTORY_WRITE_FAILED', 'no-follow history access is unavailable')
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise _error('PM_HISTORY_INVALID', 'PM runtime history is not a user file')
            os.chmod(path, 0o600, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError:
            raise _error('PM_HISTORY_INVALID', 'PM runtime history is unavailable') from None
        payload = json.dumps({'pm_session_id': self.pm_session_id, 'turns': turns[-MAX_HISTORY_TURNS:]},
                             ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode('utf-8')
        fd, temporary = tempfile.mkstemp(prefix='.' + self._key + '.', dir=self._root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                fd = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(self._root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            raise _error('PM_HISTORY_WRITE_FAILED', 'PM runtime history could not be saved') from None
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _recent_clean_close(self, now):
        path = _runtime_state_path(self._root, self._key)
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except (FileNotFoundError, OSError):
            return False
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                return False
            with os.fdopen(fd, 'r', encoding='utf-8') as stream:
                fd = -1
                state = json.load(stream)
            closed_at = datetime.fromisoformat(state['clean_closed_at']).timestamp()
            return 0 <= now - closed_at <= _LIVE_WINDOW_SECONDS
        except (KeyError, TypeError, ValueError, UnicodeError, OSError):
            return False
        finally:
            if fd >= 0:
                os.close(fd)

    def _record_clean_close(self):
        path = _runtime_state_path(self._root, self._key)
        try:
            _write_json_atomic(self._root, path,
                               {'clean_closed_at': _timestamp(self.clock)})
        except LLMToolError:
            raise _error('PM_STATE_WRITE_FAILED', 'PM runtime state could not be saved') from None

    def open(self):
        if self._child is not None:
            return self
        base_env = os.environ if self.environ is None else self.environ
        root_arg = self.state_root or Path.home() / '.local/state/llmtool/pm-runtime'
        root = _ensure_state_dir(Path(root_arg))
        binding = current_binding(self.pm_session_id, state_root=root, environ=self.environ,
                                  resolve_legacy_account=False)
        if binding is not None:
            if self.runtime != 'claude-code' and self.runtime != binding['runtime']:
                raise _error('PM_RUNTIME_UNSUPPORTED', 'PM runtime is unsupported')
            self.runtime = binding['runtime']
        if self.runtime == 'codex':
            from .pm_runtime_codex import CodexRuntime
            self._codex_driver = CodexRuntime(self)
            self._codex_driver.open()
            return self
        adapter = _ADAPTERS.get(self.runtime)
        if adapter is None:
            raise _error('PM_RUNTIME_UNSUPPORTED', 'PM runtime is unsupported')
        try:
            # Trusted user config only; the user config is under the same HOME
            # the transcript lookup uses.
            role_options = {
                'environ': self.environ,
                'home': None if self.environ is None else self.environ.get('HOME'),
            }
            if self.account is not None:
                role_options['cli'] = {'account': self.account}
            resolved_role = role_config.resolve_role('pm', **role_options)
            if binding is not None:
                bound_account = binding.get('account', resolved_role.get('account'))
                if bound_account != resolved_role.get('account'):
                    raise _error('PM_ACCOUNT_MISMATCH',
                                 'PM binding account does not match configuration')
            if resolved_role.get('provider') not in (None, 'claude'):
                raise LLMToolError('roles.pm must use a Claude account')
            config_dir = resolved_role['config_dir']
            model = resolved_role.get('model')
        except LLMToolError as exc:
            if getattr(exc, 'pm_runtime_code', None) == 'PM_ACCOUNT_MISMATCH':
                raise
            raise _error('PM_CONFIG_INVALID',
                         'pm_runtime.claude_config_dir in the user config is invalid') from None
        env = dict(base_env)
        if config_dir is not None:
            env['CLAUDE_CONFIG_DIR'] = config_dir
        try:
            key = active_pm_inbox.pm_session_key(self.pm_session_id)
            binding = current_binding(self.pm_session_id, state_root=root, environ=self.environ,
                                      resolved_account=resolved_role.get('account'))
            if (binding is not None and binding.get('dedicated') is True and
                    not binding.get('session_started')):
                if type(self.cwd) is not str:
                    raise LLMToolError('PM working directory is invalid')
                cwd = os.path.normpath(os.path.abspath(self.cwd))
                if (len(cwd) > 1024 or any(ord(char) < 0x20 or ord(char) == 0x7f for char in cwd) or
                        not os.path.isdir(cwd) or binding.get('cwd') != cwd):
                    raise LLMToolError('PM working directory does not match its dedicated binding')
                # Checked without following symlinks: a symlinked or dangling
                # component is refused even though the transcript may not exist yet.
                transcript_present = active_pm_inbox.claude_session_present(
                    self.pm_session_id, cwd, env)
            else:
                cwd = active_pm_inbox.observe_claude_session(
                    self.pm_session_id, self.cwd, env)
                transcript_present = True
        except LLMToolError:
            raise _error('PM_SESSION_INVALID', 'PM session binding could not be validated') from None
        lock_fd = _check_state_file(root / (key + '.lock'), create=True)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            raise _error('PM_SESSION_BUSY', 'PM session already has an active driver') from None
        except OSError:
            os.close(lock_fd)
            raise _error('PM_SESSION_BUSY', 'PM session lock could not be acquired') from None
        self._key, self._root, self._lock_fd = key, root, lock_fd
        try:
            refuse_fenced_session(self.pm_session_id, root)
            turns = self._history_read()
            recovered = False
            recovery_time = _timestamp(self.clock)
            for turn in turns:
                if isinstance(turn, dict) and turn.get('status') == 'ACTIVE':
                    turn['status'] = 'INTERRUPTED'
                    turn['ended_at'] = recovery_time
                    recovered = True
            if recovered:
                self._history_write(turns)
            for turn in turns:
                if isinstance(turn, dict) and isinstance(turn.get('idempotency_key'), str):
                    self._idempotency[turn['idempotency_key']] = turn
            binding = current_binding(self.pm_session_id, state_root=root, environ=self.environ,
                                      resolved_account=resolved_role.get('account'))
            if binding is None or binding.get('cwd') != cwd:
                raise _error('PM_SESSION_NOT_DEDICATED',
                             'PM session is not dedicated; create a dedicated PM session and retry')
            transcript = active_pm_inbox.claude_session_file(self.pm_session_id, cwd, env)
            clock_value = self.clock()
            now = clock_value.timestamp() if isinstance(clock_value, datetime) else float(clock_value)
            try:
                created_at = datetime.fromisoformat(binding['created_at']).timestamp()
            except (ValueError, TypeError, OverflowError):
                created_at = None
            recently_closed = self._recent_clean_close(now)
            if _session_live_elsewhere(
                    self.pm_session_id, transcript, now, created_at,
                    ignore_recent_transcript=self._has_owned_session or recently_closed):
                raise _error('PM_SESSION_LIVE_ELSEWHERE',
                             'PM session appears active elsewhere; close it before using pm_runtime')
            session_flag = ('--resume' if binding.get('session_started') or
                            transcript_present else '--session-id')
            argv = [adapter, '-p', '--input-format', 'stream-json', '--output-format',
                    'stream-json', '--verbose', '--include-partial-messages', session_flag,
                    self.pm_session_id, '--permission-prompts', 'none',
                    '--permission-mode', PM_PERMISSION_MODE,
                    '--strict-mcp-config', '--mcp-config', PM_MCP_CONFIG,
                    '--append-system-prompt', PM_SESSION_INSTRUCTION]
            if model is not None:
                argv.extend(('--model', model))
            runner = self.runner
            if runner is None:
                import subprocess
                runner = lambda args, cwd, env: subprocess.Popen(
                    args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, bufsize=0, start_new_session=True)
            self._child = runner(argv, cwd, env)
            self._lines = collections.deque()
            self._stdout_thread = threading.Thread(
                target=self._pump_stdout, args=(self._child, self._lines),
                name='llmtool-pm-stdout', daemon=True)
            self._stdout_thread.start()
            self._stderr_buffer = bytearray()
            if getattr(self._child, 'stderr', None) is not None:
                self._stderr_thread = threading.Thread(target=self._capture_stderr, daemon=True)
                self._stderr_thread.start()
            self.cwd = cwd
            self.claude_config_dir = str(transcript.parents[2])
            self._closed = False
            _register_lifecycle(self)
            self._has_owned_session = True
            return self
        except LLMToolError:
            try:
                self.close(timeout=0)
            except Exception:
                _unregister_lifecycle(self)
                self._release_lock()
                self._child = None
            self._key = self._root = None
            raise
        except Exception:
            try:
                self.close(timeout=0)
            except Exception:
                _unregister_lifecycle(self)
                self._release_lock()
                self._child = None
            self._key = self._root = None
            raise _error('PM_PROCESS_START_FAILED', 'PM runtime process could not be started') from None

    def _release_lock(self):
        fd, self._lock_fd = self._lock_fd, None
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _write_line(self, obj):
        data = (json.dumps(obj, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        with self._write_lock:
            try:
                self._child.stdin.write(data)
                self._child.stdin.flush()
            except (OSError, BrokenPipeError, AttributeError):
                raise _error('PM_PROCESS_EXITED', 'PM runtime process is unavailable') from None

    def _pump_stdout(self, child, lines):
        try:
            for raw in child.stdout:
                if isinstance(raw, (bytes, bytearray)) and len(raw) <= MAX_STREAM_LINE_BYTES:
                    raw = _Line(raw)
                    raw.arrived = _now()   # stamped before waiting for the lock
                with self._lines_cond:
                    lines.append(raw)
                    self._lines_cond.notify_all()
        except (OSError, ValueError):
            pass   # A closed or unreadable stdout ends like EOF.
        finally:
            with self._lines_cond:
                lines.append(_STDOUT_EOF)
                self._lines_cond.notify_all()

    def _read_lines(self, lines):
        """Yield stdout lines in order; EOF stays queued so every later reader sees it."""
        while True:
            raw = self._next_raw(lines)
            if raw is None:
                return
            yield raw

    def _next_raw(self, lines, deadline=None):
        """The next stdout line; None at EOF or close; _TIMED_OUT once deadline() (an
        absolute _now() time, re-read on every wake) has passed with no line buffered."""
        with self._lines_cond:
            while True:
                if lines and lines[0] is not _STDOUT_EOF:
                    return lines.popleft()
                if lines or self._closed:
                    return None
                limit = None if deadline is None else deadline()
                remaining = None if limit is None else limit - _now()
                if remaining is not None and remaining <= 0:
                    return _TIMED_OUT
                self._lines_cond.wait(remaining)

    def _starts_turn(self, raw):
        """True when an unclaimed line belongs to a turn (or must surface as an error)."""
        if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_STREAM_LINE_BYTES:
            return True
        try:
            line = json.loads(bytes(raw).decode('utf-8'))
        except (ValueError, UnicodeError):
            return True
        return (not isinstance(line, dict) or line.get('type') in _TURN_LINE_TYPES or
                _contains_different_session(line, self.pm_session_id))

    def _idle_line_starts_turn(self, raw):
        """Between turns: lifecycle frames are routed to their attempt first (a late
        frame never acknowledges a new draft); a `started` frame for any uuid begins a turn
        (a late start of an UNKNOWN message, or another client's command)."""
        if isinstance(raw, (bytes, bytearray)) and len(raw) <= MAX_STREAM_LINE_BYTES and \
                b'command_lifecycle' in raw:
            try:
                line = json.loads(bytes(raw).decode('utf-8'))
            except (ValueError, UnicodeError):
                return True
            if isinstance(line, dict) and not _contains_different_session(line, self.pm_session_id):
                frame = self._lifecycle_frame(line)
                if frame == 'invalid':
                    return False   # malformed and attached to no attempt: dropped
                if frame is not None:
                    self._late_frame(*frame)
                    return frame[1] == 'started'
        return self._starts_turn(raw)

    def _capture_stderr(self):
        stream = getattr(self._child, 'stderr', None)
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                self._stderr_buffer.extend(chunk)
                if len(self._stderr_buffer) > _STDERR_LIMIT:
                    del self._stderr_buffer[:-_STDERR_LIMIT]
        except (OSError, ValueError):
            return

    def _stderr_excerpt(self):
        value = bytes(self._stderr_buffer[-4096:]).decode('utf-8', 'replace')
        return '[withheld]' if _emitted_sensitive(value) else value

    def is_alive(self):
        if self._codex_driver is not None:
            return self._codex_driver.is_alive()
        if self._child is None or self._closed:
            return False
        try:
            return self._child.poll() is None
        except Exception:
            return False

    def send(self, text, idempotency_key=None):
        if self._codex_driver is not None:
            return self._codex_driver.send(text, idempotency_key=idempotency_key)
        if (type(text) is not str or not text or
                any((unicodedata.category(char) == 'Cc' and char not in '\t\n') or
                    unicodedata.category(char) == 'Cf' for char in text)):
            raise _error('PM_MESSAGE_INVALID', 'PM message is invalid')
        try:
            size = len(text.encode('utf-8'))
        except UnicodeError:
            size = MAX_MESSAGE_BYTES + 1
        if size > MAX_MESSAGE_BYTES or _emitted_sensitive(text):
            raise _error('PM_MESSAGE_INVALID', 'PM message is invalid')
        with self._state_lock:
            if self._closed or self._child is None:
                raise _error('PM_RUNTIME_CLOSED', 'PM runtime is not open')
            if idempotency_key is not None:
                if (type(idempotency_key) is not str or not idempotency_key or
                        len(idempotency_key.encode('utf-8')) > 256 or
                        any(unicodedata.category(c) in ('Cc', 'Cf') for c in idempotency_key)):
                    raise _error('PM_IDEMPOTENCY_KEY_INVALID', 'PM idempotency key is invalid')
                existing = self._idempotency.get(idempotency_key)
                if existing is not None:
                    return iter(({'type': 'TURN_REPLAY', 'turn_id': existing['turn_id'],
                                  'seq': existing['seq'], 'status': existing['status']},))
            if self._turn is not None:
                # Also while a background turn runs: the caller holds the message until
                # it ends, so Claude Code never folds it into that turn.
                raise _error('PM_TURN_ACTIVE', 'PM turn is already active')
            record = self._start_record(text, idempotency_key)
            if idempotency_key is not None:
                self._idempotency[idempotency_key] = record
            message_uuid = str(uuid.UUID(bytes=os.urandom(16), version=4))
            # Registered before the write: every frame for this uuid meets this attempt.
            attempt = {'uuid': message_uuid, 'record': record, 'phase': 'W',
                       'written_at': _now(), 'idle_since': None, 'result_at': None,
                       'interrupted': False, 'esc_at': None, 'bg_record': None}
            self._attempt = attempt
            # priority "later": Claude Code never folds the message into a turn that is
            # already running (a background notification turn); it starts it as a turn of
            # its own after that one ends. The uuid ties the lifecycle frames to it.
            failed = self._write_message({'type': 'user', 'uuid': message_uuid,
                                          'priority': 'later', 'message': {
                                              'role': 'user', 'content': [
                                                  {'type': 'text', 'text': text}]}})
            if failed is not None:
                # Never written (stdin already gone): NOT_SENT. Failed while writing:
                # possibly delivered, so UNKNOWN. Either way the caller holds the draft.
                letter = 'N' if failed == 'not_written' else 'X'
                status = _DELIVERY[letter]
                attempt['phase'] = letter
                self._attempt = None
                self._remember_attempt(message_uuid, letter)
                self._finish(record, status, delivery=status, reason=failed)
                return iter(({'type': 'RUNTIME_ERROR', 'turn_id': record['turn_id'], 'seq': None,
                              'code': 'PM_PROCESS_EXITED', 'stderr': self._stderr_excerpt()},
                             {'type': 'TURN_END', 'turn_id': record['turn_id'], 'seq': None,
                              'status': status, 'delivery': status, 'reason': failed,
                              'result_text': '', 'usage': {}}))
        return self._owner_events(record, attempt)

    def _write_message(self, obj):
        """Write an Owner message: None when written, 'not_written' when stdin was already
        unusable, 'uncertain' when the write or flush itself failed."""
        data = (json.dumps(obj, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        with self._write_lock:
            stdin = getattr(self._child, 'stdin', None)
            if stdin is None or getattr(stdin, 'closed', False) is True:
                return 'not_written'
            try:
                stdin.write(data)
                stdin.flush()
            except ValueError:
                return 'not_written'   # I/O on a closed file is refused before writing
            except (OSError, AttributeError):
                return 'uncertain'
        return None

    def _remember_attempt(self, message_uuid, letter):
        self._finished_attempts[message_uuid] = letter
        while len(self._finished_attempts) > 64:
            self._finished_attempts.popitem(last=False)

    def _late_frame(self, message_uuid, state):
        """A lifecycle frame for an attempt already resolved. After C, B or N no frame
        is expected: it is a protocol alert that stops automatic sends. After X a late
        frame is plausible (the message ran after all) and is only tracked."""
        letter = self._finished_attempts.get(message_uuid)
        if letter in ('C', 'B', 'N'):
            self._protocol_alerts += 1
        return letter

    def automatic_sends_blocked(self):
        """True after a lifecycle frame contradicted a latched receipt."""
        return self._protocol_alerts > 0

    def _lifecycle_frame(self, line):
        """(uuid, state) of a well-formed lifecycle frame, 'invalid' for a malformed one,
        None when the line is not a lifecycle frame."""
        if line.get('type') != 'command_lifecycle':
            return None
        message_uuid, state = line.get('command_uuid'), line.get('state')
        if (type(message_uuid) is not str or not _UUID.fullmatch(message_uuid) or
                state not in _LIFECYCLE_STATES):
            return 'invalid'
        return message_uuid, state

    def _owner_event(self, raw, attempt):
        """Normalize one stdout line for the Owner reducer: (event, foreign uuid)."""
        if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_STREAM_LINE_BYTES:
            return 'invalid', None
        try:
            line = json.loads(bytes(raw).decode('utf-8'))
        except (ValueError, UnicodeError):
            return 'invalid', None
        if not isinstance(line, dict):
            return 'invalid', None
        if _contains_different_session(line, self.pm_session_id):
            return 'fork', None
        frame = self._lifecycle_frame(line)
        if frame == 'invalid':
            return 'invalid', None
        if frame is not None:
            message_uuid, state = frame
            if message_uuid == attempt['uuid']:
                return state, None
            self._late_frame(message_uuid, state)
            return ('other_started' if state == 'started' else 'notice'), message_uuid
        kind = line.get('type')
        if kind == 'result':
            return 'result', None
        if kind in ('stream_event', 'assistant', 'user'):
            return 'output', None
        return 'notice', None

    def _owner_events(self, record, attempt):
        """Events of an Owner turn. One reducer (_owner_transition) sees every frame of the
        turn, from the write to the matching terminal lifecycle frame, including while a
        background turn or the Owner's own turn streams. Output becomes the Owner's only
        after a legal queued -> started; its result becomes delivery only at the matching
        `completed`. Every illegal transition, an expired deadline or the child's exit
        ends UNKNOWN; withdrawal before `started` ends NOT_SENT. The message is written
        once; nothing here writes it again."""
        lines = self._lines
        pushback = collections.deque()
        out = []
        flow = {'bg': None, 'bg_active': False, 'bg_seen': False, 'foreign': None,
                'owner': None, 'ended': False}

        def deadline():
            phase = attempt['phase']
            if phase == 'W':
                limit = attempt['written_at'] + PM_QUEUED_TIMEOUT
            elif phase == 'Q':
                limit = (None if flow['bg_active'] or attempt['idle_since'] is None
                         else attempt['idle_since'] + PM_STARTED_GRACE)
            elif phase in ('RO', 'RA'):
                limit = attempt['result_at'] + PM_TERMINAL_GRACE
            else:
                return None   # O / A: the turn itself may legitimately run long
            if attempt['esc_at'] is not None and phase in ('W', 'Q'):
                cancel = attempt['esc_at'] + PM_CANCEL_GRACE
                limit = cancel if limit is None else min(limit, cancel)
            return limit

        def bg_feed(raw):
            if flow['bg'] is None:
                with self._state_lock:
                    background = self._start_record('', None, origin='background', claim=False)
                    attempt['bg_record'] = background
                flow['bg'] = _TurnParser(self, background, lambda: self._was_interrupted)
                out.append({'type': 'BACKGROUND_TURN_START', 'turn_id': background['turn_id'],
                            'seq': None, 'origin': 'background'})
            flow['bg_active'] = flow['bg_seen'] = True
            events, done = flow['bg'].feed(raw)
            out.extend(events)
            if done:
                flow['bg'], flow['bg_active'], flow['foreign'] = None, False, None
                attempt['bg_record'] = None
                self._was_interrupted = False   # spent on that turn; the attempt keeps its own
            return done

        def finalize(letter, reason=None, error=None):
            if flow['ended']:
                return
            flow['ended'] = True
            with self._state_lock:
                attempt['phase'] = letter
                resolved = record.get('delivery') is not None   # close() resolved it first
                if not resolved:
                    if letter == 'C':
                        status, text, usage = flow['owner'].outcome
                        self._finish(record, status, text, usage, delivery='COMPLETED',
                                     release=False)
                    else:
                        self._finish(record, _DELIVERY[letter], delivery=_DELIVERY[letter],
                                     reason=reason, release=False)
            if error is not None and not resolved:
                out.append(error)
            if letter == 'C' and not resolved:
                status, text, usage = flow['owner'].outcome
                out.append(flow['owner'].emit('TURN_END', status=status,
                                              result_text=_safe_result_text(text), usage=usage,
                                              delivery='COMPLETED'))
                return
            fields = {'reason': record['reason']} if record.get('reason') else {}
            out.append({'type': 'TURN_END', 'turn_id': record['turn_id'], 'seq': None,
                        'status': record['status'], 'delivery': record['delivery'],
                        'result_text': '', 'usage': {}, **fields})

        def runtime_error(code, stderr=None):
            value = {'type': 'RUNTIME_ERROR', 'turn_id': record['turn_id'], 'seq': None,
                     'code': code}
            if stderr is not None:
                value['stderr'] = stderr
            return value

        def apply(event, raw, arrived, foreign):
            phase = attempt['phase']
            if event == 'notice' and foreign is not None and foreign == flow['foreign'] \
                    and flow['bg'] is None:
                flow['bg_active'], flow['foreign'] = False, None   # that command ended unseen
                if phase == 'Q':
                    attempt['idle_since'] = arrived
                return
            next_phase, action = _owner_transition(phase, event, flow['bg_active'])
            if action == 'hold_unknown':
                error = None
                if event == 'exit':
                    if flow['bg'] is not None:
                        out.extend(flow['bg'].eof())
                        flow['bg'], flow['bg_active'] = None, False
                    if not self._closed:
                        error = (flow['owner'] or _TurnParser(self, record, lambda: False)).exit_error()
                elif event == 'invalid':
                    error = runtime_error('PM_STREAM_INVALID')
                    self._terminate_child()
                elif event in ('output', 'result'):
                    pushback.appendleft(raw)   # not the Owner's: shown as background output
                elif event == 'other_started':
                    flow['bg_active'], flow['foreign'] = True, foreign
                finalize('X', error=error)
                return
            with self._state_lock:
                attempt['phase'] = next_phase
            if action == 'acknowledge':
                if not flow['bg_active']:
                    attempt['idle_since'] = arrived
            elif action == 'accept':
                if next_phase == 'O':
                    flow['owner'] = _TurnParser(self, record, lambda: attempt['interrupted'],
                                                defer=True)
                    with self._state_lock:   # persisted with the turn's end
                        record['started_at'] = _timestamp(self.clock)
                    if flow['bg_seen']:   # tells the caller its own turn begins now
                        out.append({'type': 'TURN_ACCEPTED', 'turn_id': record['turn_id'],
                                    'seq': None})
            elif action == 'retain_rejection_reason':
                finalize('N', reason=event)
            elif action in ('background_output', 'background_result'):
                if bg_feed(raw) and next_phase == 'Q':
                    attempt['idle_since'] = arrived   # the start grace restarts after it
            elif action == 'background_start':
                flow['bg_active'], flow['foreign'] = True, foreign
            elif action in ('render_output', 'save_result'):
                if phase == 'O':
                    events, _ = flow['owner'].feed(raw)
                    out.extend(events)
                else:
                    bg_feed(raw)   # A: the turn that took the message in
                if action == 'save_result':
                    attempt['result_at'] = arrived
            elif action == 'finish_owner':
                finalize('C')
            elif action == 'finish_absorbed':
                finalize('B')

        def drain_after_terminal():
            # The Owner's fate is latched. A turn still in flight is shown as background
            # until its result; later lines are left for background_turn().
            while flow['bg'] is not None or flow['bg_active'] or pushback:
                raw = pushback.popleft() if pushback else self._next_raw(lines)
                if raw is None:
                    if flow['bg'] is not None:
                        out.extend(flow['bg'].eof())
                    return
                event, foreign = self._owner_event(raw, attempt)
                if event in _LIFECYCLE_STATES:
                    self._late_frame(attempt['uuid'], event)
                elif event in ('output', 'result', 'invalid', 'fork'):
                    if bg_feed(raw):
                        return
                elif event == 'other_started':
                    flow['bg_active'] = True
                elif event == 'notice' and foreign is not None and foreign == flow['foreign'] \
                        and flow['bg'] is None:
                    flow['bg_active'] = False
                yield from out
                out.clear()

        try:
            while attempt['phase'] in _LIVE:
                raw = pushback.popleft() if pushback else self._next_raw(lines, deadline)
                if raw is _TIMED_OUT:
                    apply('timeout', None, _now(), None)
                elif raw is None:
                    apply('exit', None, _now(), None)
                else:
                    arrived = getattr(raw, 'arrived', None)
                    arrived = _now() if arrived is None else arrived
                    limit = deadline()
                    if limit is not None and arrived >= limit:
                        pushback.appendleft(raw)   # it came after the deadline had passed
                        apply('timeout', None, arrived, None)
                    else:
                        event, foreign = self._owner_event(raw, attempt)
                        if event == 'fork':
                            if flow['bg'] is not None:
                                try:
                                    self._finish(flow['bg'].record, 'ERROR')
                                except Exception:
                                    pass
                            finalize('X')
                            yield from out
                            out.clear()
                            try:
                                self.close()
                            except Exception:
                                pass
                            raise _error('PM_SESSION_FORKED',
                                         'Claude Code started a different PM session')
                        apply(event, raw, arrived, foreign)
                yield from out
                out.clear()
            yield from out
            out.clear()
            yield from drain_after_terminal()
            yield from out
        finally:
            with self._state_lock:
                if not flow['ended'] and record.get('delivery') is None:
                    try:   # abandoned mid-attempt: never left ACTIVE
                        self._finish(record, 'UNKNOWN', delivery='UNKNOWN', release=False)
                    except Exception:
                        pass
                    attempt['phase'] = 'X'
                if self._attempt is attempt:
                    self._attempt = None
                self._remember_attempt(attempt['uuid'], attempt['phase'] if
                                       attempt['phase'] in _TERMINAL else 'X')
                if self._turn is record:
                    self._turn = None
                self._lines_cond.notify_all()

    def _start_record(self, owner_text, idempotency_key, origin=None, claim=True):
        turns = self._history_read()
        record = {'turn_id': uuid.uuid4().hex, 'seq': max(
            (turn.get('seq', 0) for turn in turns if isinstance(turn, dict)), default=0) + 1,
            'started_at': _timestamp(self.clock), 'ended_at': None,
            'owner_text': owner_text, 'status': 'ACTIVE', 'result_text': '',
            'tool_names': [], 'usage': {}, 'idempotency_key': idempotency_key}
        if origin is not None:
            record['origin'] = origin
        turns.append(record)
        self._history_write(turns)
        if claim:
            self._turn = record
        return record

    def background_turn(self, timeout=None):
        """Wait up to timeout seconds for a turn Claude Code starts by itself (a background
        task notification) and return its event iterator, or None.

        Between turns, lines that are not part of a turn are dropped here. The turn is
        recorded with origin "background" and its own timing and usage; send() refuses
        with PM_TURN_ACTIVE until it ends. Raises PM_PROCESS_EXITED once the child's
        stdout has ended between turns.
        """
        if self._codex_driver is not None:
            return self._codex_driver.background_turn(timeout=timeout)
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._lines_cond:
            while True:
                lines = self._lines
                if self._closed or self._child is None or lines is None:
                    return None
                if self._turn is None:
                    while (lines and lines[0] is not _STDOUT_EOF and
                           not self._idle_line_starts_turn(lines[0])):
                        lines.popleft()
                        self._unknown_lines += 1
                    if lines and lines[0] is _STDOUT_EOF:
                        raise _error('PM_PROCESS_EXITED', 'PM runtime process has exited')
                    if lines:
                        break
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._lines_cond.wait(remaining)
            record = self._start_record('', None, origin='background')
        return self._events(record)

    def _persist_turn(self, record):
        turns = self._history_read()
        for index, item in enumerate(turns):
            if item.get('turn_id') == record['turn_id']:
                turns[index] = dict(record)
                break
        else:
            turns.append(dict(record))
        self._history_write(turns)

    def _finish(self, record, status, result_text='', usage=None, *, delivery=None,
                reason=None, release=True):
        with self._state_lock:
            record['status'] = status
            if delivery is not None:
                record['delivery'] = delivery
            if reason is not None:
                record['reason'] = reason
            record['ended_at'] = _timestamp(self.clock)
            if not result_text and record.get('result_text'):
                result_text = record['result_text']
            record['result_text'] = _safe_result_text(result_text)
            record['usage'] = usage if isinstance(usage, dict) else {}
            if status == 'COMPLETED':
                binding = current_binding(self.pm_session_id, state_root=self._root,
                                         resolve_legacy_account=False)
                if binding is not None and not binding.get('session_started'):
                    binding['session_started'] = True
                    _write_json_atomic(self._root,
                                       _binding_path(self._root, self.pm_session_id), binding)
            self._persist_turn(record)
            if release and self._turn is record:
                self._turn = None
                self._lines_cond.notify_all()   # a waiting background_turn may claim lines now

    def _events(self, record, source=None):
        """Events of one turn read from stdout (or source) until its result."""
        parser = _TurnParser(self, record, lambda: self._was_interrupted)
        try:
            for raw in (self._read_lines(self._lines) if source is None else source):
                if self._closed:
                    return
                events, done = parser.feed(raw)
                yield from events
                if done:
                    return
            yield from parser.eof()
        finally:
            self._was_interrupted = False

    def turn_active(self):
        """True while an Owner or background turn is running in this driver."""
        if self._codex_driver is not None:
            return self._codex_driver.turn_active()
        with self._state_lock:
            return self._turn is not None

    def interrupt(self):
        if self._codex_driver is not None:
            return self._codex_driver.interrupt()
        with self._state_lock:
            if self._turn is None or self._closed:
                return
            self._was_interrupted = True
            attempt, pending = self._attempt, None
            if attempt is not None and attempt['phase'] in _LIVE:
                attempt['phase'], _ = _owner_transition(attempt['phase'], 'esc', False)
                attempt['interrupted'] = True
                attempt['esc_at'] = _now()
                if attempt['phase'] in ('W', 'Q'):
                    pending = attempt['uuid']
            self._lines_cond.notify_all()   # the reducer re-reads its deadline
        self._write_line({'type': 'control_request', 'request_id': uuid.uuid4().hex,
                          'request': {'subtype': 'interrupt'}})
        if pending is not None:
            # The Owner message has not started: withdraw it too. Esc is a request, not
            # proof: only a `cancelled` frame makes it NOT_SENT; no answer ends UNKNOWN.
            self._write_line({'type': 'control_request', 'request_id': uuid.uuid4().hex,
                              'request': {'subtype': 'cancel_async_message',
                                          'message_uuid': pending}})

    def _terminate_child(self):
        child = self._child
        if child is None:
            return
        try:
            if isinstance(child, subprocess.Popen):
                os.killpg(child.pid, signal.SIGTERM)
            else:
                child.terminate()
        except Exception:
            pass

    def close(self, timeout=5):
        if self._codex_driver is not None:
            return self._codex_driver.close(timeout=timeout)
        with self._state_lock:
            if (self._closed and self._lock_fd is None and self._child is None and
                    self not in _active_runtimes and self._turn is None):
                return
            self._closed = True
            child = self._child
            active_turn = self._turn
            attempt = self._attempt
            self._lines_cond.notify_all()
        error = None
        had_child = child is not None
        if active_turn is not None:
            try:
                with self._state_lock:
                    if active_turn.get('delivery') is not None:
                        if self._turn is active_turn:   # resolved already; just release it
                            self._turn = None
                    elif attempt is not None and attempt['record'] is active_turn:
                        # A written message whose fate is unconfirmed: UNKNOWN, held by the
                        # caller (its iterator reports it once), never INTERRUPTED.
                        background = attempt.get('bg_record')
                        if background is not None and background.get('status') == 'ACTIVE':
                            self._finish(background, 'INTERRUPTED')
                        self._finish(active_turn, 'UNKNOWN', delivery='UNKNOWN')
                    else:
                        self._finish(active_turn, 'INTERRUPTED')
            except Exception as exc:
                error = exc
        try:
            if child is not None:
                try:
                    child.stdin.close()
                except Exception:
                    pass
                self._terminate_child()
                try:
                    child.wait(timeout=timeout)
                except Exception:
                    try:
                        self._terminate_child()
                    except Exception:
                        pass
                    try:
                        child.wait(timeout=timeout)
                    except Exception:
                        try:
                            if isinstance(child, subprocess.Popen):
                                os.killpg(child.pid, signal.SIGKILL)
                            else:
                                child.kill()
                        except Exception:
                            pass
                        try:
                            child.wait()
                        except Exception:
                            pass
        finally:
            self._child = None
            try:
                _unregister_lifecycle(self)
            except Exception as exc:
                if error is None:
                    error = exc
            if error is None and had_child and self._key is not None:
                try:
                    self._record_clean_close()
                except Exception as exc:
                    error = exc
            try:
                self._release_lock()
            except Exception as exc:
                if error is None:
                    error = exc
        if error is not None:
            raise error


def read_history(pm_session_id, *, after=0, limit=50, latest=None, state_root=None):
    try:
        key = active_pm_inbox.pm_session_key(pm_session_id)
    except LLMToolError:
        raise _error('PM_SESSION_INVALID', 'PM session ID is invalid') from None
    if (type(after) is not int or after < 0 or type(limit) is not int or limit < 1 or
            (latest is not None and (type(latest) is not int or latest < 1))):
        raise _error('PM_HISTORY_QUERY_INVALID', 'PM history paging is invalid')
    root_arg = state_root or Path.home() / '.local/state/llmtool/pm-runtime'
    root = _ensure_state_dir(Path(root_arg))
    path = _history_path(root, key)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        turns = []
    except OSError:
        raise _error('PM_HISTORY_INVALID', 'PM runtime history is unavailable') from None
    else:
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise _error('PM_HISTORY_INVALID', 'PM runtime history is not a user file')
            with os.fdopen(fd, 'r', encoding='utf-8') as stream:
                fd = -1
                payload = json.load(stream)
            if (not isinstance(payload, dict) or payload.get('pm_session_id') != pm_session_id or
                    not isinstance(payload.get('turns'), list)):
                raise _error('PM_HISTORY_INVALID', 'PM runtime history is malformed')
            turns = payload['turns']
        except (ValueError, UnicodeError, OSError):
            raise _error('PM_HISTORY_INVALID', 'PM runtime history is malformed') from None
        finally:
            if fd >= 0:
                os.close(fd)
    if latest is None:
        selected = [turn for turn in turns if isinstance(turn, dict) and
                    type(turn.get('seq')) is int and turn['seq'] > after][:limit]
    else:
        selected = [turn for turn in turns if isinstance(turn, dict) and
                    type(turn.get('seq')) is int][-latest:]
    next_after = selected[-1]['seq'] if selected else after
    return {'pm_session_id': pm_session_id, 'turns': selected, 'next_after': next_after}
