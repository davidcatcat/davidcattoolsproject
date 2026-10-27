"""Bounded driver for an already-running Claude Code PM session."""

import fcntl
import atexit
import json
import os
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

from . import active_pm_inbox
from .errors import LLMToolError
from .test_output_excerpts import _emitted_sensitive

MAX_MESSAGE_BYTES = 16384
MAX_STREAM_LINE_BYTES = 1024 * 1024
MAX_HISTORY_TURNS = 200
_ADAPTERS = {'claude-code': 'claude'}
_LIVE_WINDOW_SECONDS = 20
_STDERR_LIMIT = 65536
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


def _history_path(root, key):
    return root / (key + '.history.json')


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


def current_binding(pm_session_id=None, *, state_root=None):
    """Read a persisted dedicated-session binding, or the most recently created one."""
    root_arg = state_root or Path.home() / '.local/state/llmtool/pm-runtime'
    root = _ensure_state_dir(Path(root_arg))
    if pm_session_id is not None:
        native_id = pm_session_id
    else:
        pointer = root / 'current-binding.json'
        try:
            with pointer.open(encoding='utf-8') as stream:
                native_id = json.load(stream).get('native_session_id')
        except (OSError, ValueError, AttributeError):
            return None
    if type(native_id) is not str or not active_pm_inbox.PM_NATIVE_SESSION_ID.fullmatch(native_id):
        return None
    path = _binding_path(root, native_id)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, encoding='utf-8') as stream:
            binding = json.load(stream)
    except (OSError, ValueError):
        return None
    expected = {'runtime': 'claude-code', 'native_session_id': native_id,
                'dedicated': True}
    if (not isinstance(binding, dict) or any(binding.get(k) != v for k, v in expected.items()) or
            type(binding.get('dedicated')) is not bool or type(binding.get('cwd')) is not str or
            type(binding.get('created_at')) is not str):
        return None
    return binding


def create_dedicated_pm(cwd, *, state_root=None, environ=None, clock=None, runner=None):
    """Start a fresh Claude session and durably bind it as the dedicated PM."""
    try:
        cwd = os.path.normpath(os.path.abspath(cwd))
        if not os.path.isdir(cwd):
            raise ValueError()
    except (OSError, TypeError, ValueError):
        raise _error('PM_SESSION_INVALID', 'PM working directory is invalid') from None
    native_id = str(uuid.uuid4())
    argv = ['claude', '--session-id', native_id]
    env = dict(os.environ if environ is None else environ)
    try:
        if runner is None:
            subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True)
        else:
            runner(argv, cwd, env)
    except Exception:
        raise _error('PM_PROCESS_START_FAILED', 'Claude PM session could not be started') from None
    root_arg = state_root or Path.home() / '.local/state/llmtool/pm-runtime'
    root = _ensure_state_dir(Path(root_arg))
    binding = {'runtime': 'claude-code', 'native_session_id': native_id, 'cwd': cwd,
               'dedicated': True, 'created_at': _timestamp(clock or time.time)}
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


def _terminate_all(signum=None, frame=None):
    previous = _previous_signal_handlers.get(signum, signal.SIG_DFL)
    for runtime in tuple(_active_runtimes):
        runtime.close()
    if signum is None or previous is signal.SIG_IGN:
        return
    if callable(previous):
        previous(signum, frame)
        return
    # The old disposition is now restored by the final runtime close. Re-sending
    # the signal lets the operating system apply its original default action.
    os.kill(os.getpid(), signum)


def _restore_signal_handlers():
    global _signal_handlers_installed
    for signum, handler in tuple(_previous_signal_handlers.items()):
        try:
            # Do not overwrite a handler installed by another owner while this
            # runtime was open. The cache can be stale if that owner replaced
            # our handler, so the live disposition is authoritative.
            if signal.getsignal(signum) is _terminate_all:
                signal.signal(signum, handler)
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


class PMRuntime:
    """Drive exactly one bound Claude PM session at a time."""

    def __init__(self, pm_session_id, *, cwd, runtime='claude-code', runner=None,
                 state_root=None, environ=None, clock=None):
        self.pm_session_id = pm_session_id
        self.cwd = cwd
        self.runtime = runtime
        self.runner = runner
        self.state_root = state_root
        self.environ = environ
        self.clock = clock or time.time
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
        self._stderr_buffer = bytearray()
        self._stderr_thread = None
        self._idempotency = {}

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

    def open(self):
        if self._child is not None:
            return self
        adapter = _ADAPTERS.get(self.runtime)
        if adapter is None:
            raise _error('PM_RUNTIME_UNSUPPORTED', 'PM runtime is unsupported')
        try:
            cwd = active_pm_inbox.observe_claude_session(
                self.pm_session_id, self.cwd, self.environ)
            key = active_pm_inbox.pm_session_key(self.pm_session_id)
        except LLMToolError:
            raise _error('PM_SESSION_INVALID', 'PM session binding could not be validated') from None
        root_arg = self.state_root or Path.home() / '.local/state/llmtool/pm-runtime'
        root = _ensure_state_dir(Path(root_arg))
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
            for turn in self._history_read():
                if isinstance(turn, dict) and isinstance(turn.get('idempotency_key'), str):
                    self._idempotency[turn['idempotency_key']] = turn
            binding = current_binding(self.pm_session_id, state_root=root)
            if binding is None or binding.get('cwd') != cwd:
                raise _error('PM_SESSION_NOT_DEDICATED',
                             'PM session is not dedicated; create a dedicated PM session and retry')
            transcript = active_pm_inbox.claude_session_file(self.pm_session_id, cwd, self.environ)
            clock_value = self.clock()
            now = clock_value.timestamp() if isinstance(clock_value, datetime) else float(clock_value)
            try:
                created_at = datetime.fromisoformat(binding['created_at']).timestamp()
            except (ValueError, TypeError, OverflowError):
                created_at = None
            if _session_live_elsewhere(
                    self.pm_session_id, transcript, now, created_at,
                    ignore_recent_transcript=self._has_owned_session):
                raise _error('PM_SESSION_LIVE_ELSEWHERE',
                             'PM session appears active elsewhere; close it before using pm_runtime')
            env = dict(os.environ if self.environ is None else self.environ)
            argv = [adapter, '-p', '--input-format', 'stream-json', '--output-format',
                    'stream-json', '--verbose', '--include-partial-messages', '--resume',
                    self.pm_session_id, '--permission-prompts', 'none']
            runner = self.runner
            if runner is None:
                import subprocess
                runner = lambda args, cwd, env: subprocess.Popen(
                    args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, bufsize=0, start_new_session=True)
            self._child = runner(argv, cwd, env)
            self._stderr_buffer = bytearray()
            if getattr(self._child, 'stderr', None) is not None:
                self._stderr_thread = threading.Thread(target=self._capture_stderr, daemon=True)
                self._stderr_thread.start()
            self.cwd = cwd
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
        if self._child is None or self._closed:
            return False
        try:
            return self._child.poll() is None
        except Exception:
            return False

    def send(self, text, idempotency_key=None):
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
                raise _error('PM_TURN_ACTIVE', 'PM turn is already active')
            turns = self._history_read()
            record = {'turn_id': uuid.uuid4().hex, 'seq': max(
                (turn.get('seq', 0) for turn in turns if isinstance(turn, dict)), default=0) + 1,
                'started_at': _timestamp(self.clock), 'ended_at': None,
                'owner_text': text, 'status': 'ACTIVE', 'result_text': '',
                'tool_names': [], 'usage': {}, 'idempotency_key': idempotency_key}
            turns.append(record)
            self._history_write(turns)
            self._turn = record
            if idempotency_key is not None:
                self._idempotency[idempotency_key] = record
            try:
                self._write_line({'type': 'user', 'message': {'role': 'user', 'content': [
                    {'type': 'text', 'text': text}]}})
            except Exception:
                record['status'] = 'ERROR'
                record['ended_at'] = _timestamp(self.clock)
                self._persist_turn(record)
                self._turn = None
                raise
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

    def _finish(self, record, status, result_text='', usage=None):
        with self._state_lock:
            record['status'] = status
            record['ended_at'] = _timestamp(self.clock)
            record['result_text'] = _safe_result_text(result_text)
            record['usage'] = usage if isinstance(usage, dict) else {}
            self._persist_turn(record)
            if self._turn is record:
                self._turn = None

    def _events(self, record):
        child = self._child
        event_seq = 0

        def emit(kind, **fields):
            nonlocal event_seq
            value = {'type': kind, 'turn_id': record['turn_id'], 'seq': event_seq}
            event_seq += 1
            value.update(fields)
            return value

        try:
            for raw in child.stdout:
                if self._closed:
                    return
                if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_STREAM_LINE_BYTES:
                    yield emit('RUNTIME_ERROR', code='PM_STREAM_INVALID')
                    self._terminate_child()
                    self._finish(record, 'ERROR')
                    yield emit('TURN_END', status='ERROR', result_text='', usage={})
                    return
                try:
                    line = json.loads(bytes(raw).decode('utf-8'))
                except (ValueError, UnicodeError):
                    line = None
                if not isinstance(line, dict):
                    yield emit('RUNTIME_ERROR', code='PM_STREAM_INVALID')
                    self._terminate_child()
                    self._finish(record, 'ERROR')
                    yield emit('TURN_END', status='ERROR', result_text='', usage={})
                    return
                if _contains_different_session(line, self.pm_session_id):
                    self._terminate_child()
                    self._finish(record, 'ERROR')
                    self._release_lock()
                    self._closed = True
                    raise _error('PM_SESSION_FORKED', 'Claude Code started a different PM session')
                kind = line.get('type')
                if kind == 'stream_event':
                    event = line.get('event') or {}
                    delta = event.get('delta') or {}
                    if event.get('type') == 'content_block_delta' and delta.get('type') == 'text_delta':
                        text = delta.get('text')
                        if isinstance(text, str):
                            yield emit('TEXT_DELTA', text=text,
                                       block_index=event.get('index', 0))
                elif kind == 'assistant':
                    for item in _iter_content((line.get('message') or {}).get('content')):
                        if not isinstance(item, dict):
                            continue
                        if item.get('type') == 'text' and isinstance(item.get('text'), str):
                            yield emit('ASSISTANT_TEXT', text=item['text'], final=True)
                        elif item.get('type') == 'tool_use':
                            tool_id, name = item.get('id'), item.get('name')
                            if isinstance(name, str):
                                if name not in record['tool_names']:
                                    record['tool_names'].append(name)
                                yield emit('TOOL_USE', tool_use_id=tool_id, name=name)
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
                            yield emit('TOOL_RESULT', tool_use_id=item.get('tool_use_id'),
                                       reason=reason, is_error=is_error)
                elif kind == 'result':
                    status = 'INTERRUPTED' if self._was_interrupted else (
                        'ERROR' if line.get('is_error') or line.get('subtype') in
                        ('error', 'error_during_execution') else 'COMPLETED')
                    result = line.get('result', '')
                    result_text = result if isinstance(result, str) else ''
                    usage = line.get('usage') if isinstance(line.get('usage'), dict) else {}
                    if status == 'ERROR':
                        lower = result_text.lower()
                        code = ('PM_QUOTA_EXHAUSTED' if any(word in lower for word in
                                ('quota exceeded', 'usage limit', 'rate limit', 'hit your limit'))
                                else 'PM_PROCESS_EXITED')
                        yield emit('RUNTIME_ERROR', code=code, stderr=self._stderr_excerpt())
                    self._finish(record, status, result_text, usage)
                    yield emit('TURN_END', status=status,
                               result_text=_safe_result_text(result_text), usage=usage)
                    return
                else:
                    self._unknown_lines += 1
            if self._turn is record:
                if self._stderr_thread is not None:
                    self._stderr_thread.join(timeout=1)
                stderr = self._stderr_excerpt()
                lower = stderr.lower()
                code = ('PM_QUOTA_EXHAUSTED' if any(word in lower for word in
                        ('quota exceeded', 'usage limit', 'rate limit')) else 'PM_PROCESS_EXITED')
                yield emit('RUNTIME_ERROR', code=code, stderr=stderr)
                self._finish(record, 'ERROR')
                yield emit('TURN_END', status='ERROR', result_text='', usage={})
        finally:
            self._was_interrupted = False

    def interrupt(self):
        with self._state_lock:
            if self._turn is None or self._closed:
                return
            self._was_interrupted = True
        self._write_line({'type': 'control_request', 'request_id': uuid.uuid4().hex,
                          'request': {'subtype': 'interrupt'}})

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
        with self._state_lock:
            if self._closed and self._lock_fd is None:
                return
            self._closed = True
            child = self._child
            active_turn = self._turn
        if active_turn is not None:
            self._finish(active_turn, 'INTERRUPTED')
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
            _unregister_lifecycle(self)
            self._release_lock()
            self._child = None


def read_history(pm_session_id, *, after=0, limit=50, state_root=None):
    try:
        key = active_pm_inbox.pm_session_key(pm_session_id)
    except LLMToolError:
        raise _error('PM_SESSION_INVALID', 'PM session ID is invalid') from None
    if type(after) is not int or after < 0 or type(limit) is not int or limit < 1:
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
    selected = [turn for turn in turns if isinstance(turn, dict) and
                type(turn.get('seq')) is int and turn['seq'] > after][:limit]
    next_after = selected[-1]['seq'] if selected else after
    return {'pm_session_id': pm_session_id, 'turns': selected, 'next_after': next_after}
