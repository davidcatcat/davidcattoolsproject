"""Bounded, non-interactive OpenCode Developer invocation for one D6 task.

This is a direct CLI adapter, not the D8 Host/Worker route. The caller owns task
admission and durable TaskStore claims. OpenCode receives only a task-local config
and the current configured auth location; this module never reads credential data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import subprocess
import tempfile
import time

from .errors import LLMToolError
from . import worker_dispatch
from .opencode_worker import _observe_exit_without_reap, _path
from .providers.cli_lifecycle import CLIProcessLifecycle
from .test_output_excerpts import _emitted_sensitive

MODEL = 'opencode/deepseek-v4.1-flash-free#high'
AGENT = 'llmtool-direct-developer'
MAX_WALL_CLOCK_SECONDS = 600
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_PROMPT_BYTES = 64 * 1024
MAX_OUTPUT_TOKENS = 32768
DEFAULT_AGENT_STEPS = 12
MIN_AGENT_STEPS = 3
MAX_AGENT_STEPS = 50
MAX_SOURCE_FILE_BYTES = 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 2 * 1024 * 1024
MAX_VERSION_OUTPUT_BYTES = 4096
VERSION_TIMEOUT_SECONDS = 5
_SESSION = re.compile(r'ses_[A-Za-z0-9]{1,100}\Z')
# Any well-formed release is accepted; event compatibility is enforced by the
# strict JSON event parser rather than by pinning one CLI version.
_VERSION = re.compile(r'opencode v[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}'
                      r'(?:-[0-9A-Za-z.-]{1,64})?(?:\+[0-9A-Za-z.-]{1,64})?\Z')
# Only OpenCode-hosted free models; a paid model is never selected or a fallback.
_FREE_MODEL = re.compile(
    r'opencode/[A-Za-z0-9][A-Za-z0-9._-]{0,94}-free(?:#[a-z]{1,16})?\Z')


class DirectOpenCodeError(LLMToolError):
    """A bounded, non-sensitive adapter failure with a stable reason code."""

    def __init__(self, code, *, attempted=False, cleanup_status='UNKNOWN',
                 process_exit_code=None, stdout_sha256=None, elapsed_ms=None,
                 source_bytes=None, source_file_count=None,
                 source_manifest_sha256=None, task_id=None, run_id=None,
                 started_at=None, ended_at=None, native_session_id=None):
        super().__init__('OpenCode Developer execution unavailable; details omitted')
        self.direct_opencode_code = code
        self.attempted = attempted
        self.cleanup_status = cleanup_status
        self.process_exit_code = process_exit_code
        self.stdout_sha256 = stdout_sha256
        self.elapsed_ms = elapsed_ms
        self.source_bytes = source_bytes
        self.source_file_count = source_file_count
        self.source_manifest_sha256 = source_manifest_sha256
        self.task_id = task_id
        self.run_id = run_id
        self.started_at = started_at
        self.ended_at = ended_at
        self.native_session_id = native_session_id
        self.event_summary = None


@dataclass(frozen=True)
class DirectResult:
    exit_code: int
    session_id: str | None
    text: str | None
    usage: dict | None
    usage_source: str
    stdout_sha256: str
    elapsed_ms: int
    cleanup_status: str
    cli_version: str
    source_bytes: int
    source_file_count: int
    source_manifest_sha256: str
    task_id: str | None = None
    run_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    event_summary: dict | None = None


def _reject(code):
    raise DirectOpenCodeError(code)


def is_free_model(model):
    return type(model) is str and _FREE_MODEL.match(model) is not None


def validate_model(model):
    if not is_free_model(model):
        _reject('OPENCODE_MODEL_INVALID')
    return model


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


_DENIAL_CODES = frozenset(('TOOL_DENIED', 'PERMISSION_DENIED',
                           'PermissionDeniedError', 'PermissionRejectedError'))


def _tool_denied(event):
    """Recognize only structured refusal markers, never provider prose."""
    if type(event) is not dict:
        return False
    if event.get('type') in ('permission.asked', 'permission_requested'):
        # This route has no interactive approval channel.
        return True
    error = event.get('error')
    part = event.get('part')
    state = part.get('state') if type(part) is dict else None
    if event.get('type') == 'tool_use' and type(state) is dict:
        if state.get('status') in ('denied', 'rejected'):
            return True
        error = state.get('error')
    return (type(error) is dict and
            (type(error.get('code')) is str and error['code'] in _DENIAL_CODES or
             type(error.get('name')) is str and error['name'] in _DENIAL_CODES))


def _has_structured_tool_denial(raw):
    """Inspect a failed CLI result without retaining or returning its content."""
    try:
        for line in raw.decode('utf-8').splitlines():
            if line and _tool_denied(json.loads(line)):
                return True
    except (UnicodeError, ValueError, RecursionError):
        return False
    return False


def _has_provider_auth_error(raw):
    """Recognize the provider auth error type without retaining provider prose."""
    try:
        for line in raw.decode('utf-8').splitlines():
            if not line:
                continue
            event = json.loads(line)
            error = event.get('error') if type(event) is dict else None
            if (type(event) is dict and event.get('type') == 'error' and
                    type(error) is dict and error.get('type') == 'provider.auth'):
                return True
    except (UnicodeError, ValueError, RecursionError):
        return False
    return False


def _has_output_truncation(events, *, stderr=False):
    """Recognize bounded truncation markers without retaining provider output."""
    try:
        if stderr:
            decoded = events.decode('utf-8', 'replace')
            return ('StepFailedError' in decoded or
                    'Response still truncated' in decoded)
        for event in events:
            if type(event) is not dict or event.get('type') != 'error':
                continue
            error = event.get('error')
            if type(error) is not dict:
                continue
            # Error messages are provider prose. Only the fixed CLI fallback
            # marker may select a code when the provider type is unknown.
            if (error.get('type') == 'output-limit' or
                    (error.get('type') == 'unknown' and
                     type(error.get('message')) is str and
                     'Response still truncated' in error['message'])):
                return True
    except (ValueError, RecursionError):
        return False
    return False


def _json_event_prefix(raw):
    """Decode valid JSON events up to the first malformed line."""
    events = []
    complete = True
    for raw_line in raw.splitlines():
        try:
            line = raw_line.decode('utf-8')
        except UnicodeError:
            return events, False
        if not line:
            continue
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            return events, False
        if type(event) is not dict:
            # A valid non-object is invalid for the strict event parser, but
            # later objects still carry useful structured failure evidence.
            complete = False
            continue
        events.append(event)
    return events, complete


def _recovered_transport_indices(events):
    """Return transport errors recovered by a same-session step finish.

    The original OpenCode stream was not retained, so this recovery rule is
    inferred from the strongest available structured completion evidence.
    """
    recovered = set()
    finished_sessions = set()
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        error = event.get('error')
        session_id = event.get('sessionID')
        if (event.get('type') != 'error' or type(error) is not dict or
                error.get('type') != 'provider.transport' or
                type(session_id) is not str or _SESSION.fullmatch(session_id) is None):
            if (event.get('type') == 'step_finish' and
                    type(session_id) is str and
                    _SESSION.fullmatch(session_id) is not None):
                finished_sessions.add(session_id)
            continue
        if session_id in finished_sessions:
            recovered.add(index)
    return recovered


def _classify_provider_events(events, *, stderr=b''):
    """Classify structured OpenCode errors without retaining provider text."""
    if any(_tool_denied(event) for event in events):
        return 'TOOL_DENIED'
    if any(event.get('type') == 'error' and
           type(event.get('error')) is dict and
           event['error'].get('type') == 'provider.auth'
           for event in events):
        return 'OPENCODE_AUTH_REQUIRED'
    has_error = False
    has_free_limit = False
    has_rate_limit = False
    recovered = _recovered_transport_indices(events)
    for index, event in enumerate(events):
        if event.get('type') != 'error':
            continue
        error = event.get('error')
        error_type = error.get('type') if type(error) is dict else None
        if index not in recovered:
            has_error = True
        if type(error) is not dict:
            continue
        if error_type in ('provider.free-limit', 'provider.quota'):
            has_free_limit = True
        if error_type == 'provider.rate-limit':
            has_rate_limit = True
        status = error.get('status', error.get('statusCode',
                            error.get('status_code')))
        code = error.get('code')
        if status == 429 or code == 429 or code == '429':
            has_rate_limit = True
    if has_free_limit:
        return 'OPENCODE_FREE_LIMIT'
    if has_rate_limit:
        return 'OPENCODE_RATE_LIMITED'
    if (_has_output_truncation(events) or
            _has_output_truncation(stderr, stderr=True)):
        return 'OPENCODE_OUTPUT_TRUNCATED'
    return 'OPENCODE_PROVIDER_FAILED' if has_error else None


def _classify_provider_error(raw, *, stderr=b''):
    """Classify structured OpenCode errors, preserving valid events pre-truncation."""
    events, _ = _json_event_prefix(raw)
    return _classify_provider_events(events, stderr=stderr)


def _observed_native_session(raw):
    """Return a single syntactically valid CLI session ID, or UNKNOWN."""
    sid = None
    try:
        for line in raw.decode('utf-8').splitlines():
            if not line:
                continue
            event = json.loads(line)
            if type(event) is not dict:
                return None
            current = event.get('sessionID')
            if current is None:
                continue
            if (type(current) is not str or _SESSION.fullmatch(current) is None or
                    (sid is not None and sid != current)):
                return None
            sid = current
    except (UnicodeError, ValueError, RecursionError):
        return None
    return sid


def _observed_run_id(store, task_id):
    """Read an existing TaskStore binding when the caller supplies that interface."""
    getter = getattr(store, 'get', None)
    if not callable(getter):
        return None
    try:
        record = getter(task_id)
    except Exception:
        _reject('OPENCODE_D6_BINDING_CHANGED')
    if (type(record) is not dict or record.get('task_id') != task_id or
            type(record.get('run_id')) is not str or
            re.fullmatch(r'[0-9a-f]{32}', record['run_id']) is None):
        _reject('OPENCODE_D6_BINDING_CHANGED')
    return record['run_id']


def _valid_paths(root, read_paths, write_paths):
    if (type(read_paths) is not list or type(write_paths) is not list or
            not read_paths or len(read_paths) > 32 or len(write_paths) > 32 or
            read_paths != sorted(set(read_paths)) or
            write_paths != sorted(set(write_paths)) or
            not set(write_paths).issubset(read_paths)):
        _reject('OPENCODE_SCOPE_INVALID')
    root = Path(root)
    for name in read_paths:
        try:
            _path(name)
        except Exception:
            _reject('OPENCODE_SCOPE_INVALID')
        target = root / name
        for ancestor in (target, *target.parents):
            if ancestor == root:
                break
            try:
                if ancestor.is_symlink():
                    _reject('OPENCODE_SCOPE_INVALID')
            except OSError:
                _reject('OPENCODE_SCOPE_INVALID')
        try:
            if root not in target.resolve(strict=False).parents:
                _reject('OPENCODE_SCOPE_INVALID')
        except OSError:
            _reject('OPENCODE_SCOPE_INVALID')


def _read_scoped_file(root, name):
    """Read one bounded regular file through no-follow directory descriptors."""
    parts = name.split('/')
    directory_flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | os.O_NOFOLLOW
    root_fd = os.open(root, directory_flags)
    current_fd = root_fd
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = next_fd
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=current_fd)
        except FileNotFoundError:
            return None
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    before.st_size < 0):
                _reject('OPENCODE_SOURCE_INVALID')
            if before.st_size > MAX_SOURCE_FILE_BYTES:
                _reject('OPENCODE_SOURCE_TOO_LARGE')
            raw = bytearray()
            while True:
                chunk = os.read(fd, min(65536, MAX_SOURCE_FILE_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > MAX_SOURCE_FILE_BYTES:
                    _reject('OPENCODE_SOURCE_TOO_LARGE')
            after = os.fstat(fd)
            identity = lambda info: (info.st_dev, info.st_ino, info.st_mode,
                                     info.st_nlink, info.st_size,
                                     info.st_mtime_ns, info.st_ctime_ns)
            if identity(before) != identity(after) or len(raw) != before.st_size:
                _reject('OPENCODE_SOURCE_CHANGED')
            try:
                text = bytes(raw).decode('utf-8')
            except UnicodeError:
                _reject('OPENCODE_SOURCE_INVALID')
            return {
                'path': name, 'device': before.st_dev, 'inode': before.st_ino,
                'size': before.st_size, 'mtime_ns': before.st_mtime_ns,
                'ctime_ns': before.st_ctime_ns,
                'sha256': hashlib.sha256(raw).hexdigest(),
            }, len(raw)
        finally:
            os.close(fd)
    finally:
        if current_fd != root_fd:
            os.close(current_fd)
        os.close(root_fd)


def _screen_sources(root, read_paths):
    """Screen and bind every source file before launching any OpenCode process."""
    entries = []
    total = 0
    for name in read_paths:
        try:
            captured = _read_scoped_file(root, name)
        except DirectOpenCodeError:
            raise
        except OSError:
            _reject('OPENCODE_SOURCE_UNAVAILABLE')
        if captured is None:
            entries.append({'path': name, 'exists': False})
            continue
        entry, size = captured
        total += size
        if total > MAX_SOURCE_TOTAL_BYTES:
            _reject('OPENCODE_SOURCE_TOO_LARGE')
        entries.append(entry)
    encoded = json.dumps(entries, ensure_ascii=True, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('ascii')
    return total, len(entries), hashlib.sha256(encoded).hexdigest(), entries


def _validate_steps(steps):
    if type(steps) is not int or not MIN_AGENT_STEPS <= steps <= MAX_AGENT_STEPS:
        _reject('OPENCODE_STEPS_INVALID')
    return steps


def configuration(*, read_paths, write_paths, model=MODEL,
                   steps=DEFAULT_AGENT_STEPS):
    """Return the exact task permission envelope consumed by OpenCode V2."""
    selected = validate_model(model)
    steps = _validate_steps(steps)
    rules = [{'action': '*', 'resource': '*', 'effect': 'deny'}]
    rules.extend({'action': 'read', 'resource': path, 'effect': 'allow'}
                 for path in read_paths)
    rules.extend({'action': 'edit', 'resource': path, 'effect': 'allow'}
                 for path in write_paths)
    provider, model = selected.split('/', 1)
    model = model.split('#', 1)[0]
    return {
        'model': selected,
        'default_agent': AGENT,
        'share': 'disabled',
        'snapshots': False,
        'providers': {provider: {'models': {model: {
            'limit': {'output': MAX_OUTPUT_TOKENS},
        }}}},
        'formatter': False,
        'lsp': False,
        'warming': False,
        'compaction': {'auto': False},
        'skills': [],
        'instructions': [],
        'mcp': {'servers': {}},
        'permissions': rules,
        'policies': [
            {'action': 'provider.use', 'resource': '*', 'effect': 'deny'},
            {'action': 'provider.use', 'resource': 'opencode', 'effect': 'allow'},
            {'action': 'integration.use', 'resource': '*', 'effect': 'deny'},
            # #1605 Owner A: OpenCode 2.2.6+ routes the free model via this one.
            {'action': 'integration.use', 'resource': 'opencode#*', 'effect': 'allow'},
        ],
        'agents': {
            # Read then edit need two tool-capable rounds before the final summary.
            AGENT: {'mode': 'primary', 'model': selected, 'steps': steps,
                    'permissions': rules},
            'title': {'disabled': True},
            'summary': {'disabled': True},
            'compaction': {'disabled': True},
        },
    }


def _screen_prompt(prompt):
    if type(prompt) is not str or not prompt.strip():
        _reject('OPENCODE_PROMPT_INVALID')
    if _emitted_sensitive(prompt):
        _reject('OPENCODE_PROMPT_SENSITIVE')
    try:
        raw = prompt.encode('utf-8')
    except UnicodeError:
        _reject('OPENCODE_PROMPT_INVALID')
    if len(raw) > MAX_PROMPT_BYTES:
        _reject('OPENCODE_PROMPT_TOO_LARGE')
    return raw


def _safe_environment(config_file, config_dir):
    """Pass only normal OpenCode auth locations and non-secret runtime settings."""
    home = os.path.realpath(os.path.expanduser('~'))
    if not os.path.isabs(home) or not os.path.isdir(home):
        _reject('OPENCODE_HOME_UNAVAILABLE')
    env = {
        'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
        'HOME': home,
        'OPENCODE_CONFIG': str(config_file),
        'OPENCODE_CONFIG_DIR': str(config_dir),
        'OPENCODE_CONFIG_PROJECT_DISABLE': '1',
        'OPENCODE_DISABLE_CLAUDE_CODE': '1',
        'OPENCODE_DISABLE_FFF': '1',
        'OPENCODE_FILEWATCHER_DISABLE': '1',
        'NO_COLOR': '1',
        'TERM': 'dumb',
    }
    # Respect a caller's configured OpenCode data/auth location by path only.
    data_home = os.environ.get('XDG_DATA_HOME')
    if data_home and os.path.isabs(data_home) and '\x00' not in data_home:
        env['XDG_DATA_HOME'] = data_home
    return env


def _await_exit_without_reap(process, *, deadline):
    remaining = max(0, deadline - time.monotonic())
    try:
        exited = _observe_exit_without_reap(process, remaining, deadline)
    except Exception:
        _reject('OPENCODE_CLEANUP_UNKNOWN')
    if not exited:
        _reject('OPENCODE_TIMEOUT')


def parse_json_events(raw, *, step_limit=DEFAULT_AGENT_STEPS):
    """Validate OpenCode's newline-delimited JSON output; never infer success from prose."""
    step_limit = _validate_steps(step_limit)
    if type(raw) is not bytes or not raw:
        _reject('OPENCODE_OUTPUT_INVALID')
    if len(raw) > MAX_OUTPUT_BYTES:
        _reject('OPENCODE_OUTPUT_TOO_LARGE')
    events, complete = _json_event_prefix(raw)
    classified = _classify_provider_events(events)
    if classified is not None:
        _reject(classified)
    if not complete:
        _reject('OPENCODE_OUTPUT_INVALID')
    sid = None
    text = []
    input_tokens = output_tokens = 0
    usage_known = True
    step_finishes = 0
    recovered = _recovered_transport_indices(events)
    for index, event in enumerate(events):
        if _tool_denied(event):
            _reject('TOOL_DENIED')
        event_type = event.get('type')
        event_sid = event.get('sessionID')
        allowed_type = (event_type in ('step_start', 'step_finish', 'text', 'tool_use') or
                        (event_type == 'error' and index in recovered))
        if (not allowed_type or
                type(event_sid) is not str or _SESSION.fullmatch(event_sid) is None or
                (sid is not None and sid != event_sid)):
            _reject('OPENCODE_OUTPUT_INVALID')
        sid = event_sid
        if event_type == 'text':
            part = event.get('part')
            value = part.get('text') if type(part) is dict else event.get('text')
            if type(value) is str:
                text.append(value)
        if event_type == 'step_finish':
            step_finishes += 1
            part = event.get('part')
            tokens = part.get('tokens') if type(part) is dict else event.get('tokens')
            if type(tokens) is dict:
                incoming, outgoing = tokens.get('input'), tokens.get('output')
                if (type(incoming) is int and type(outgoing) is int and
                        0 <= incoming <= 10**9 and 0 <= outgoing <= 10**9 and
                        incoming + outgoing > 0):
                    input_tokens += incoming
                    output_tokens += outgoing
                else:
                    usage_known = False
            else:
                usage_known = False
    if sid is None:
        _reject('OPENCODE_OUTPUT_INCOMPLETE')
    output_text = ''.join(text)
    if not output_text and step_finishes >= step_limit:
        _reject('OPENCODE_STEPS_EXHAUSTED')
    usage = (None if not usage_known or step_finishes == 0 else {
        'prompt_tokens': input_tokens,
        'completion_tokens': output_tokens,
        'total_tokens': input_tokens + output_tokens,
    })
    return sid, output_text or None, usage


def summarize_events(raw, *, step_limit=DEFAULT_AGENT_STEPS):
    """Return a bounded integer summary of the valid JSON event prefix."""
    try:
        step_limit_value = _validate_steps(step_limit)
    except Exception:
        step_limit_value = DEFAULT_AGENT_STEPS
    if type(raw) is not bytes:
        events = []
    else:
        try:
            events, _ = _json_event_prefix(raw)
        except Exception:
            events = []
    event_counts = {}
    step_finishes = 0
    text_chars = 0
    tool_use_count = 0
    edit_tool_uses = 0
    edited_files = set()
    for event in events:
        event_type = event.get('type') if type(event) is dict else None
        if event_type == 'step_start':
            event_counts['step_start'] = event_counts.get('step_start', 0) + 1
        elif event_type == 'step_finish':
            event_counts['step_finish'] = event_counts.get('step_finish', 0) + 1
            step_finishes += 1
        elif event_type == 'text':
            event_counts['text'] = event_counts.get('text', 0) + 1
            part = event.get('part')
            value = part.get('text') if type(part) is dict else event.get('text')
            if type(value) is str:
                text_chars += len(value)
        elif event_type == 'tool_use':
            event_counts['tool_use'] = event_counts.get('tool_use', 0) + 1
            tool_use_count += 1
            part = event.get('part')
            tool = part.get('tool') if type(part) is dict else None
            if tool in ('edit', 'write', 'patch'):
                edit_tool_uses += 1
                state = part.get('state') if type(part) is dict else None
                input_state = state.get('input') if type(state) is dict else None
                file_path = input_state.get('filePath') if type(input_state) is dict else None
                if type(file_path) is str:
                    edited_files.add(file_path)
        elif event_type == 'error':
            event_counts['error'] = event_counts.get('error', 0) + 1
        else:
            event_counts['other'] = event_counts.get('other', 0) + 1
    return {
        'event_counts': {key: count for key, count in event_counts.items() if count},
        'step_finishes': step_finishes,
        'step_limit': step_limit_value,
        'text_chars': text_chars,
        'tool_use_count': tool_use_count,
        'edit_tool_uses': edit_tool_uses,
        'edited_file_count': len(edited_files),
    }


def _version(executable):
    started = time.monotonic()
    deadline = started + VERSION_TIMEOUT_SECONDS
    lifecycle = CLIProcessLifecycle('opencode-cli', deadline=deadline + 3)
    process = None
    output = bytearray()
    try:
        with lifecycle:
            selector = None
            try:
                try:
                    process = subprocess.Popen([executable, '--version'], stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
                        env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
                             'HOME': os.path.realpath(os.path.expanduser('~'))},
                        close_fds=True, start_new_session=True, bufsize=0)
                    lifecycle.own(process)
                except Exception:
                    _reject('OPENCODE_RUNTIME_UNAVAILABLE')
                selector = selectors.DefaultSelector()
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    lifecycle.check()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        _reject('OPENCODE_TIMEOUT')
                    for key, _ in selector.select(min(remaining, 0.1)):
                        try:
                            chunk = os.read(key.fileobj.fileno(),
                                            MAX_VERSION_OUTPUT_BYTES + 1 - len(output))
                        except (BlockingIOError, InterruptedError):
                            continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output.extend(chunk)
                        if len(output) > MAX_VERSION_OUTPUT_BYTES:
                            _reject('OPENCODE_VERSION_OUTPUT_TOO_LARGE')
                _await_exit_without_reap(process, deadline=deadline)
            finally:
                if selector is not None:
                    selector.close()
                if process is not None and process.stdout is not None:
                    process.stdout.close()
    except DirectOpenCodeError:
        raise
    except Exception:
        _reject('OPENCODE_RUNTIME_UNAVAILABLE')
    value = bytes(output).decode('utf-8', 'replace').strip()
    if (not lifecycle.cleanup_confirmed or process is None or process.returncode != 0 or
            _VERSION.match(value) is None):
        _reject('OPENCODE_VERSION_UNSUPPORTED')
    return value


def run(*, root, read_paths, write_paths, prompt, prelaunch_validate,
        dispatch_store=None, dispatch_task_id=None,
        launch_callback=None,
        timeout_seconds=MAX_WALL_CLOCK_SECONDS, executable=None, model=MODEL,
        steps=DEFAULT_AGENT_STEPS):
    """Invoke one bounded OpenCode run in the existing D6 task worktree."""
    validate_model(model)
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        _reject('OPENCODE_WORKTREE_INVALID')
    _valid_paths(root, read_paths, write_paths)
    payload = _screen_prompt(prompt)
    source_bytes, source_file_count, source_manifest_sha256, source_manifest = \
        _screen_sources(root, read_paths)
    if not callable(prelaunch_validate):
        _reject('OPENCODE_D6_BINDING_CHANGED')
    if dispatch_store is None or type(dispatch_task_id) is not str:
        _reject('WORKER_DISPATCH_CONTEXT_UNAVAILABLE')
    if (type(timeout_seconds) is not int or
            not 1 <= timeout_seconds <= MAX_WALL_CLOCK_SECONDS):
        _reject('OPENCODE_BUDGET_INVALID')
    executable = executable or shutil.which('opencode')
    if not executable or not os.path.isabs(executable):
        _reject('OPENCODE_RUNTIME_UNAVAILABLE')
    version = _version(executable)
    _, _, current_manifest_sha256, current_manifest = _screen_sources(root, read_paths)
    if (current_manifest_sha256 != source_manifest_sha256 or
            current_manifest != source_manifest):
        _reject('OPENCODE_SOURCE_CHANGED')
    config = configuration(read_paths=read_paths, write_paths=write_paths,
                           model=model, steps=steps)
    config_text = json.dumps(config, ensure_ascii=True, sort_keys=True,
                             separators=(',', ':'), allow_nan=False)
    started = time.monotonic()
    started_at = None
    run_id = None
    work_deadline = started + timeout_seconds
    cleanup_deadline = work_deadline + 3
    stdout, stderr = bytearray(), bytearray()
    process = None
    cleanup = 'UNKNOWN'
    with tempfile.TemporaryDirectory(prefix='llmtool-opencode-config-') as config_dir:
        os.chmod(config_dir, 0o700)
        config_file = Path(config_dir) / 'opencode.json'
        try:
            fd = os.open(config_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(config_text)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            _reject('OPENCODE_CONFIG_UNAVAILABLE')
        env = _safe_environment(config_file, config_dir)
        argv = [executable, 'run', '--standalone', '--model', model, '--agent', AGENT,
                '--format', 'json', '--title', 'llmtool-task-developer']
        lifecycle = CLIProcessLifecycle('opencode-cli', deadline=cleanup_deadline)
        try:
            with lifecycle:
                selector = None
                try:
                    try:
                        valid = prelaunch_validate()
                    except Exception:
                        _reject('OPENCODE_D6_BINDING_CHANGED')
                    if valid is not True:
                        _reject('OPENCODE_D6_BINDING_CHANGED')
                    try:
                        worker_dispatch.authorize_dispatch(
                            dispatch_store, dispatch_task_id, 'opencode-developer', 'DISPATCH')
                    except worker_dispatch.WorkerDispatchDenied as exc:
                        raise DirectOpenCodeError(exc.dispatch_code) from None
                    run_id = _observed_run_id(dispatch_store, dispatch_task_id)
                    try:
                        process = subprocess.Popen(argv, cwd=str(root), env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, bufsize=0, shell=False,
                            close_fds=True, start_new_session=True)
                        started_at = _utc_now()
                        lifecycle.own(process)
                    except Exception:
                        _reject('OPENCODE_RUNTIME_UNAVAILABLE')
                    if launch_callback is not None:
                        try:
                            from .codex_direct_developer import process_start_time
                            launch_callback(process.pid, process_start_time(process.pid),
                                            os.getpgid(process.pid))
                        except Exception:
                            _reject('OPENCODE_LAUNCH_RECORD_REJECTED')
                    selector = selectors.DefaultSelector()
                    for stream, name in ((process.stdout, 'stdout'),
                                         (process.stderr, 'stderr')):
                        os.set_blocking(stream.fileno(), False)
                        selector.register(stream, selectors.EVENT_READ, name)
                    os.set_blocking(process.stdin.fileno(), False)
                    selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
                    offset = 0
                    denial_line_start = 0
                    while selector.get_map():
                        lifecycle.check()
                        remaining = work_deadline - time.monotonic()
                        if remaining <= 0:
                            _reject('OPENCODE_TIMEOUT')
                        for key, _ in selector.select(min(remaining, 0.1)):
                            if key.data == 'stdin':
                                try:
                                    offset += os.write(key.fileobj.fileno(), payload[offset:offset + 8192])
                                except (BrokenPipeError, OSError):
                                    offset = len(payload)
                                if offset >= len(payload):
                                    selector.unregister(key.fileobj)
                                    key.fileobj.close()
                                continue
                            try:
                                chunk = os.read(key.fileobj.fileno(), 8192)
                            except (BlockingIOError, InterruptedError):
                                continue
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            destination = stdout if key.data == 'stdout' else stderr
                            cap = MAX_OUTPUT_BYTES if key.data == 'stdout' else MAX_STDERR_BYTES
                            chunk_start = len(stdout) if key.data == 'stdout' else 0
                            if len(destination) + len(chunk) > cap:
                                _reject('OPENCODE_OUTPUT_TOO_LARGE' if key.data == 'stdout'
                                        else 'OPENCODE_STDERR_TOO_LARGE')
                            destination.extend(chunk)
                            if key.data == 'stdout':
                                scan_from = 0
                                while True:
                                    end = chunk.find(b'\n', scan_from)
                                    if end < 0:
                                        break
                                    line_end = chunk_start + end + 1
                                    complete = bytes(stdout[denial_line_start:line_end])
                                    denial_line_start = line_end
                                    scan_from = end + 1
                                    if _has_structured_tool_denial(complete):
                                        _reject('TOOL_DENIED')
                    _await_exit_without_reap(process, deadline=work_deadline)
                finally:
                    if selector is not None:
                        selector.close()
                    if process is not None:
                        for stream in (process.stdin, process.stdout, process.stderr):
                            if stream is not None:
                                try:
                                    stream.close()
                                except OSError:
                                    pass
            cleanup = 'CONFIRMED' if lifecycle.cleanup_confirmed else 'UNKNOWN'
        except DirectOpenCodeError as exc:
            cleanup = 'CONFIRMED' if lifecycle.cleanup_confirmed else 'UNKNOWN'
            exc.cleanup_status = cleanup
            exc.attempted = process is not None
            exc.process_exit_code = (process.returncode if process is not None and
                                     process.returncode is not None else None)
            exc.stdout_sha256 = (hashlib.sha256(bytes(stdout)).hexdigest()
                                 if stdout else None)
            exc.elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
            exc.source_bytes = source_bytes
            exc.source_file_count = source_file_count
            exc.source_manifest_sha256 = source_manifest_sha256
            exc.task_id = dispatch_task_id
            exc.run_id = run_id
            exc.started_at = started_at
            exc.ended_at = _utc_now() if started_at is not None else None
            exc.native_session_id = _observed_native_session(bytes(stdout))
            raise
        except BaseException as exc:
            cleanup = 'CONFIRMED' if lifecycle.cleanup_confirmed else 'UNKNOWN'
            try:
                exc._llmtool_direct_opencode_execution = {
                    'process_exit_code': (process.returncode if process is not None else None),
                    'stdout_sha256': (hashlib.sha256(bytes(stdout)).hexdigest()
                                      if stdout else None),
                    'elapsed_ms': max(0, int((time.monotonic() - started) * 1000)),
                    'cleanup_status': cleanup,
                    'source_bytes': source_bytes,
                    'source_file_count': source_file_count,
                    'source_manifest_sha256': source_manifest_sha256,
                    'task_id': dispatch_task_id,
                    'run_id': run_id,
                    'started_at': started_at,
                    'ended_at': _utc_now() if started_at is not None else None,
                    'native_session_id': _observed_native_session(bytes(stdout)),
                }
            except Exception:
                pass
            raise
    elapsed = max(0, int((time.monotonic() - started) * 1000))
    ended_at = _utc_now() if started_at is not None else None
    raw = bytes(stdout)
    if cleanup != 'CONFIRMED':
        exc = DirectOpenCodeError('OPENCODE_CLEANUP_UNKNOWN', attempted=True,
                                  cleanup_status=cleanup,
                                  process_exit_code=(process.returncode if process else None),
                                  stdout_sha256=(hashlib.sha256(raw).hexdigest() if raw else None),
                                  elapsed_ms=elapsed, source_bytes=source_bytes,
                                  source_file_count=source_file_count,
                                  source_manifest_sha256=source_manifest_sha256,
                                  task_id=dispatch_task_id, run_id=run_id,
                                  started_at=started_at, ended_at=ended_at,
                                  native_session_id=_observed_native_session(raw))
        raise exc
    if process is None or process.returncode is None:
        raise DirectOpenCodeError('OPENCODE_EXIT_UNKNOWN', attempted=process is not None,
                                  cleanup_status=cleanup,
                                  stdout_sha256=(hashlib.sha256(raw).hexdigest() if raw else None),
                                  elapsed_ms=elapsed, source_bytes=source_bytes,
                                  source_file_count=source_file_count,
                                  source_manifest_sha256=source_manifest_sha256,
                                  task_id=dispatch_task_id, run_id=run_id,
                                  started_at=started_at, ended_at=ended_at,
                                  native_session_id=_observed_native_session(raw))
    if process.returncode != 0:
        raise DirectOpenCodeError(
                                  _classify_provider_error(raw, stderr=bytes(stderr)) or
                                  'OPENCODE_PROVIDER_FAILED', attempted=True,
                                  cleanup_status=cleanup, process_exit_code=process.returncode,
                                  stdout_sha256=(hashlib.sha256(raw).hexdigest() if raw else None),
                                  elapsed_ms=elapsed, source_bytes=source_bytes,
                                  source_file_count=source_file_count,
                                  source_manifest_sha256=source_manifest_sha256,
                                  task_id=dispatch_task_id, run_id=run_id,
                                  started_at=started_at, ended_at=ended_at,
                                  native_session_id=_observed_native_session(raw))
    try:
        session_id, text, usage = parse_json_events(raw, step_limit=steps)
    except DirectOpenCodeError as exc:
        exc.attempted = True
        exc.cleanup_status = cleanup
        exc.process_exit_code = process.returncode
        exc.stdout_sha256 = hashlib.sha256(raw).hexdigest()
        exc.event_summary = summarize_events(raw, step_limit=steps)
        exc.elapsed_ms = elapsed
        exc.source_bytes = source_bytes
        exc.source_file_count = source_file_count
        exc.source_manifest_sha256 = source_manifest_sha256
        exc.task_id = dispatch_task_id
        exc.run_id = run_id
        exc.started_at = started_at
        exc.ended_at = ended_at
        exc.native_session_id = _observed_native_session(raw)
        raise
    return DirectResult(process.returncode, session_id, text, usage,
                        'CLI_REPORTED' if usage is not None else 'UNKNOWN',
                        hashlib.sha256(raw).hexdigest(), elapsed, cleanup, version,
                        source_bytes, source_file_count, source_manifest_sha256,
                        dispatch_task_id, run_id, started_at, ended_at,
                        event_summary=summarize_events(raw, step_limit=steps))
