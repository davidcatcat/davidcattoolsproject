"""Per-turn Codex CLI adapter for a dedicated llmtool PM binding."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import threading
import uuid
from pathlib import Path

from . import active_pm_inbox, role_config
from .errors import LLMToolError

_THREAD_ID = re.compile(r'[0-9a-fA-F-]{16,64}\Z')
_ALLOWED_ENV = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE',
                'SSL_CERT_FILE', 'SSL_CERT_DIR')
_MAX_LINE = 1024 * 1024


def _error(code):
    from .pm_runtime import _error as make_error
    return make_error(code, 'Codex PM runtime is unavailable')


class CodexRuntime:
    """Use PMRuntime's persistent state, history, lock and event conventions."""

    def __init__(self, owner):
        self.owner = owner
        self._thread_id = None
        self._active = False
        self._child = None
        self._interrupted = False
        self._mutex = threading.RLock()
        self._closed = False

    def open(self):
        from . import pm_runtime
        owner = self.owner
        root_arg = owner.state_root or Path.home() / '.local/state/llmtool/pm-runtime'
        root = pm_runtime._ensure_state_dir(Path(root_arg))
        binding = pm_runtime.current_binding(owner.pm_session_id, state_root=root,
                                             environ=owner.environ,
                                             resolve_legacy_account=False)
        if binding is None or binding.get('runtime') != 'codex':
            raise _error('PM_SESSION_NOT_DEDICATED')
        try:
            account = self._bound_account_name(binding)
            options = {'environ': owner.environ,
                       'home': None if owner.environ is None else owner.environ.get('HOME'),
                       'cli': {'account': account}}
            resolved = role_config.resolve_role('pm-codex-binding', **options)
            if (resolved.get('provider') != 'codex' or
                    resolved.get('account') != account or not resolved.get('config_dir')):
                raise LLMToolError('PM account is not Codex')
            if account != resolved.get('account') or binding.get('account') != account:
                raise _error('PM_ACCOUNT_MISMATCH')
        except LLMToolError as exc:
            if getattr(exc, 'pm_runtime_code', None) == 'PM_ACCOUNT_MISMATCH':
                raise
            raise _error('PM_CONFIG_INVALID') from None
        cwd = os.path.normpath(os.path.abspath(owner.cwd))
        if (binding.get('cwd') != cwd or not os.path.isdir(cwd) or len(cwd) > 1024 or
                any(ord(char) < 32 or ord(char) == 127 for char in cwd)):
            raise _error('PM_SESSION_INVALID')
        native_id = owner.pm_session_id
        key = active_pm_inbox.pm_session_key(native_id)
        lock_fd = pm_runtime._check_state_file(root / (key + '.lock'), create=True)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(lock_fd)
            raise _error('PM_SESSION_BUSY') from None
        owner._key, owner._root, owner._lock_fd = key, root, lock_fd
        owner.cwd = cwd
        owner._closed = False
        self._closed = False
        try:
            pm_runtime.refuse_fenced_session(native_id, root)
            self._read_state()
            owner._history_read()
            if binding.get('session_started') or self._thread_id is not None:
                if self._thread_id is None:
                    raise _error('PM_SESSION_INVALID')
                active_pm_inbox.observe_codex_session(self._thread_id, cwd,
                                                      self._environment())
                if self._live_elsewhere(self._thread_id):
                    raise _error('PM_SESSION_LIVE_ELSEWHERE')
        except Exception:
            owner._release_lock()
            owner._key = owner._root = None
            raise
        pm_runtime._register_lifecycle(owner)
        return self

    @staticmethod
    def _live_elsewhere(thread_id):
        try:
            listing = subprocess.check_output(['ps', '-axo', 'pid=,command='],
                                              stderr=subprocess.DEVNULL, timeout=2)
            for line in listing.decode('utf-8', 'replace').splitlines():
                try:
                    _, command = line.strip().split(None, 1)
                except ValueError:
                    continue
                try:
                    argv = shlex.split(command)
                except ValueError:
                    continue
                executable = os.path.basename(argv[0]).lower() if argv else ''
                if (executable == 'codex' and thread_id in argv[1:] and
                        ('exec' in argv or 'resume' in argv)):
                    return True
        except (OSError, subprocess.SubprocessError):
            pass
        return False

    def _bound_account_name(self, binding):
        account = self.owner.account if self.owner.account is not None else binding.get('account')
        if (type(account) is not str or not role_config._name(account) or
                binding.get('account') != account):
            raise _error('PM_ACCOUNT_MISMATCH')
        return account

    def _state_path(self):
        from . import pm_runtime
        return pm_runtime._runtime_state_path(self.owner._root, self.owner._key)

    def _read_state(self):
        path = self._state_path()
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except FileNotFoundError:
            return
        except OSError:
            raise _error('PM_STATE_INVALID') from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise _error('PM_STATE_INVALID')
            with os.fdopen(fd, encoding='utf-8') as stream:
                fd = -1
                value = json.load(stream)
            thread_id = value.get('codex_thread_id') if type(value) is dict else None
            if thread_id is not None:
                if type(thread_id) is not str or not _THREAD_ID.fullmatch(thread_id):
                    raise _error('PM_STATE_INVALID')
                self._thread_id = thread_id
        except (ValueError, UnicodeError, OSError):
            raise _error('PM_STATE_INVALID') from None
        finally:
            if fd >= 0:
                os.close(fd)

    def _save_state(self, **fields):
        from . import pm_runtime
        path = self._state_path()
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except FileNotFoundError:
            value = {}
        except (OSError, ValueError, UnicodeError):
            raise _error('PM_STATE_INVALID') from None
        else:
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    raise _error('PM_STATE_INVALID')
                with os.fdopen(fd, encoding='utf-8') as stream:
                    fd = -1
                    value = json.load(stream)
                if type(value) is not dict:
                    raise ValueError()
            except (OSError, ValueError, UnicodeError):
                raise _error('PM_STATE_INVALID') from None
            finally:
                if fd >= 0:
                    os.close(fd)
        value.update(fields)
        pm_runtime._write_json_atomic(self.owner._root, path, value)

    def _environment(self):
        source = os.environ if self.owner.environ is None else self.owner.environ
        env = {key: source[key] for key in _ALLOWED_ENV if key in source}
        env.setdefault('PATH', os.environ.get('PATH', '/usr/bin:/bin'))
        from . import pm_runtime
        root_arg = self.owner.state_root or Path.home() / '.local/state/llmtool/pm-runtime'
        binding = pm_runtime.current_binding(self.owner.pm_session_id, state_root=root_arg,
                                             environ=self.owner.environ,
                                             resolve_legacy_account=False)
        if binding is None or binding.get('runtime') != 'codex':
            raise _error('PM_SESSION_NOT_DEDICATED')
        account = self._bound_account_name(binding)
        options = {'environ': self.owner.environ,
                   'home': None if self.owner.environ is None else self.owner.environ.get('HOME'),
                   'cli': {'account': account}}
        resolved = role_config.resolve_role('pm-codex-binding', **options)
        if (resolved.get('provider') != 'codex' or resolved.get('account') != account or
                not resolved.get('config_dir')):
            raise _error('PM_CONFIG_INVALID')
        if resolved.get('config_dir') is not None:
            env['CODEX_HOME'] = resolved['config_dir']
        for key in ('HOME', 'CODEX_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR'):
            if key in env and (not os.path.isabs(env[key]) or
                               any(ord(char) < 32 or ord(char) == 127 for char in env[key])):
                raise _error('PM_CONFIG_INVALID')
        from . import github_identity
        home = env.get('HOME') or os.path.expanduser('~')
        return github_identity.ai_github_env(env, home)

    def send(self, text, idempotency_key=None):
        from . import pm_runtime
        owner = self.owner
        try:
            invalid_text = (type(text) is not str or not text or
                            any(ord(char) < 32 and char not in '\t\n' for char in text) or
                            len(text.encode('utf-8')) > pm_runtime.MAX_MESSAGE_BYTES or
                            pm_runtime._emitted_sensitive(text))
        except (UnicodeError, TypeError):
            invalid_text = True
        if invalid_text:
            raise _error('PM_MESSAGE_INVALID')
        with self._mutex, owner._state_lock:
            if self._closed:
                raise _error('PM_RUNTIME_CLOSED')
            if owner._turn is not None or self._active:
                raise _error('PM_TURN_ACTIVE')
            if idempotency_key is not None:
                try:
                    invalid_key = (type(idempotency_key) is not str or not idempotency_key or
                                   len(idempotency_key.encode('utf-8')) > 256 or
                                   any(ord(char) < 32 or ord(char) == 127
                                       for char in idempotency_key))
                except (UnicodeError, AttributeError):
                    invalid_key = True
                if invalid_key:
                    raise _error('PM_IDEMPOTENCY_KEY_INVALID')
                existing = next((turn for turn in owner._history_read()
                                 if turn.get('idempotency_key') == idempotency_key), None)
                if existing is not None:
                    return iter(({'type': 'TURN_REPLAY', 'turn_id': existing['turn_id'],
                                  'seq': existing['seq'], 'status': existing['status']},))
            record = owner._start_record(text, idempotency_key)
            self._active = True
            self._interrupted = False
            try:
                process = self._launch(text)
            except Exception:
                self._active = False
                owner._finish(record, 'ERROR')
                raise
            self._child = process
            owner._child = process
        return self._events(record, process)

    def _launch(self, text):
        executable = shutil.which('codex', path=self._environment().get('PATH'))
        if executable is None or not os.path.isabs(executable):
            raise _error('PM_PROCESS_START_FAILED')
        argv = [executable, '--cd', self.owner.cwd, '--sandbox', 'workspace-write',
                '--no-daemon', '--ask-for-approval', 'never', 'exec']
        if self._thread_id is not None:
            argv.extend(('resume', self._thread_id))
        argv.extend(('--json', '-c', 'sandbox_workspace_write.network_access=true', '-'))
        try:
            child = subprocess.Popen(argv, cwd=self.owner.cwd, env=self._environment(),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
            child.stdin.write((text + '\n').encode('utf-8'))
            child.stdin.close()
            return child
        except (OSError, BrokenPipeError):
            if 'child' in locals():
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=1)
                except Exception:
                    pass
            raise _error('PM_PROCESS_START_FAILED') from None

    def _events(self, record, process):
        from . import pm_runtime
        seq = 0
        result_text = ''
        thread_id = self._thread_id
        tools = set()
        error = None

        def event(kind, **fields):
            nonlocal seq
            value = {'type': kind, 'turn_id': record['turn_id'], 'seq': seq}
            seq += 1
            value.update(fields)
            return value

        try:
            while True:
                raw = process.stdout.readline(_MAX_LINE + 1)
                if not raw:
                    break
                if len(raw) > _MAX_LINE:
                    error = 'PM_STREAM_INVALID'
                    break
                try:
                    item = json.loads(raw.decode('utf-8'))
                except (ValueError, UnicodeError):
                    error = 'PM_STREAM_INVALID'
                    break
                if type(item) is not dict:
                    error = 'PM_STREAM_INVALID'
                    break
                kind = item.get('type')
                if kind == 'thread.started':
                    candidate = item.get('thread_id')
                    if (type(candidate) is not str or not _THREAD_ID.fullmatch(candidate) or
                            thread_id is not None and candidate != thread_id):
                        error = 'PM_SESSION_FORKED'
                        break
                    thread_id = candidate
                    self._thread_id = candidate
                    self._save_state(codex_thread_id=candidate)
                elif kind == 'item.started':
                    content = item.get('item')
                    if type(content) is dict and content.get('type') in (
                            'command_execution', 'tool_call', 'function_call', 'mcp_tool_call'):
                        tool_id = content.get('id') or uuid.uuid4().hex
                        name = content.get('name') or content.get('type')
                        if name not in record['tool_names']:
                            record['tool_names'].append(name)
                        tools.add(tool_id)
                        yield event('TOOL_USE', tool_use_id=tool_id, name=name,
                                    description=pm_runtime._tool_description(
                                        name, content, self.owner.cwd))
                elif kind == 'item.completed':
                    content = item.get('item')
                    if type(content) is dict and content.get('type') == 'agent_message':
                        text = content.get('text')
                        if type(text) is not str:
                            error = 'PM_STREAM_INVALID'
                            break
                        screened = pm_runtime._screen_stream_text(text)
                        if screened != '[withheld]':
                            result_text = pm_runtime._bounded_stream_append(result_text, screened)
                            record['result_text'] = result_text
                            yield event('TEXT_DELTA', text=screened, block_index=0)
                            yield event('ASSISTANT_TEXT', text=screened, final=True)
                    elif type(content) is dict and content.get('id') in tools:
                        yield event('TOOL_RESULT', tool_use_id=content.get('id'),
                                    reason='OK', is_error=False)
                elif kind == 'turn.completed':
                    usage_value = item.get('usage')
                    usage = usage_value if type(usage_value) is dict else {}
                    status = 'INTERRUPTED' if self._interrupted else 'COMPLETED'
                    self.owner._finish(record, status, result_text, usage)
                    yield event('TURN_END', status=status,
                                result_text=pm_runtime._safe_result_text(result_text), usage=usage)
                    return
                elif kind == 'item.failed':
                    failed = item.get('item')
                    if type(failed) is dict and failed.get('type') in (
                            'command_execution', 'tool_call', 'function_call', 'mcp_tool_call'):
                        yield event('TOOL_RESULT', tool_use_id=failed.get('id'),
                                    reason='TOOL_ERROR', is_error=True)
                elif kind in ('error', 'turn.failed'):
                    error = 'PM_PROCESS_EXITED'
                    break
            # EOF can become readable just before poll() observes the child's
            # exit status. A failed process takes precedence over a malformed
            # final stream line, matching the process-exit classification used
            # by the Claude adapter when its child exits during a turn.
            try:
                exit_code = process.wait(timeout=5)
            except Exception:
                exit_code = process.poll()
            if exit_code not in (0, None) and error is None:
                error = 'PM_PROCESS_EXITED'
            elif error is None:
                error = 'PM_STREAM_INVALID'
        finally:
            try:
                process.stdout.close()
            except (AttributeError, OSError):
                pass
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except OSError:
                    pass
            try:
                process.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                except Exception:
                    pass
            self._child = None
            self.owner._child = None
            with self._mutex, self.owner._state_lock:
                self._active = False
                if self.owner._turn is record and record.get('status') == 'ACTIVE':
                    self.owner._finish(record, 'ERROR')
                if self.owner._turn is record:
                    self.owner._turn = None
        if error and self._interrupted:
            status = 'INTERRUPTED'
            self.owner._finish(record, status, result_text, {})
            yield event('TURN_END', status=status,
                        result_text=pm_runtime._safe_result_text(result_text), usage={})
        elif error:
            yield event('RUNTIME_ERROR', code=error)
            yield event('TURN_END', status='ERROR', result_text='', usage={})

    def interrupt(self):
        with self._mutex:
            self._interrupted = True
            child = self._child
            if child is not None and child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGINT)
                except OSError:
                    pass

    def turn_active(self):
        return self._active

    def is_alive(self):
        return (not self._closed and self._child is not None and
                self._child.poll() is None)

    def background_turn(self, timeout=None):
        del timeout
        return None

    def close(self, timeout=5):
        with self._mutex:
            self._closed = True
            child = self._child
            if child is not None and child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except OSError:
                    pass
                try:
                    child.wait(timeout=timeout)
                except Exception:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=timeout)
                    except Exception:
                        pass
            if self._active and self.owner._turn is not None:
                try:
                    self.owner._finish(self.owner._turn, 'INTERRUPTED')
                except Exception:
                    pass
            self._active = False
            self._child = None
            self.owner._child = None
            self.owner._turn = None
            try:
                from .pm_runtime import _timestamp
                self._save_state(clean_closed_at=_timestamp(self.owner.clock))
            except Exception:
                pass
            self.owner._closed = True
            from .pm_runtime import _unregister_lifecycle
            _unregister_lifecycle(self.owner)
            self.owner._release_lock()
