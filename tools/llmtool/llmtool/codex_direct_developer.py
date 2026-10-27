"""Bounded Codex CLI Developer invocation for one registered D6 task."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import tempfile
import time

from . import worker_dispatch
from .errors import LLMToolError
from .providers.cli_lifecycle import CLIHostTermination, CLIProcessLifecycle

MAX_WALL_CLOCK_SECONDS = 600
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_PROMPT_BYTES = 64 * 1024
_THREAD_ID = re.compile(r'[0-9a-fA-F-]{16,64}\Z')
_EFFORT = re.compile(r'(?:minimal|low|medium|high|xhigh)\Z')
_TOOL_CALL_ITEM_TYPES = frozenset((
    'command_execution', 'tool_call', 'function_call', 'mcp_tool_call',
))


class DirectCodexError(LLMToolError):
    def __init__(self, code, *, attempted=False, process_exit_code=None,
                 stdout_sha256=None, elapsed_ms=None, cleanup_status='UNKNOWN',
                 native_session_id=None, usage=None):
        super().__init__('Codex Developer execution unavailable; details omitted')
        self.direct_codex_code = code
        self.attempted = attempted
        self.process_exit_code = process_exit_code
        self.stdout_sha256 = stdout_sha256
        self.elapsed_ms = elapsed_ms
        self.cleanup_status = cleanup_status
        self.native_session_id = native_session_id
        self.usage = usage


@dataclass(frozen=True)
class DirectResult:
    status: str
    summary: str
    tests: tuple
    self_review: str
    blocked_reason: str | None
    exit_code: int
    thread_id: str
    stdout_sha256: str
    elapsed_ms: int
    cleanup_status: str
    usage: dict | None


def _fail(code):
    raise DirectCodexError(code)


def adapter_error_code(exc, prefix='CODEX_ADAPTER_'):
    """Stable, content-free error code naming an unexpected exception.

    A D6/Git binding stop keeps its own completion code; anything else is
    named by its exception class. Messages, paths and prompt text never
    reach the code.
    """
    code = getattr(exc, 'completion_code', None)
    if type(code) is not str or not re.fullmatch(r'[A-Z0-9_]{1,48}', code):
        code = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', type(exc).__name__).upper()
        code = re.sub(r'[^A-Z0-9_]', '_', code) or 'EXCEPTION'
    return (prefix + code)[:64]


# Codex runs its shell commands with no inherited environment, so the command
# PATH is explicit: the registered worktree's llmtool virtualenv first (the
# ENV-2 bootstrapped interpreter the Task's tests need), then fixed system
# directories. Without it every command fails with "command not found".
_SYSTEM_COMMAND_PATH = ('/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin')


def _command_path(root):
    venv_bin = Path(root) / 'tools' / 'llmtool' / '.venv' / 'bin'
    entries = list(_SYSTEM_COMMAND_PATH)
    if venv_bin.is_dir() and not venv_bin.is_symlink():
        entries.insert(0, str(venv_bin))
    return ':'.join(entries)


def _thread_from_events(raw):
    """Accept the actual Codex JSONL thread.started event only."""
    found = None
    first = True
    try:
        for line in raw.decode('utf-8').splitlines():
            if not line:
                continue
            event = json.loads(line, object_pairs_hook=_unique_keys,
                                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(event) is not dict:
                return None
            if first and event.get('type') != 'thread.started':
                return None
            first = False
            if event.get('type') == 'thread.started':
                current = event.get('thread_id')
                if type(current) is not str or not _THREAD_ID.fullmatch(current):
                    return None
                if found is not None and current != found:
                    return None
                found = current
    except (UnicodeError, ValueError, RecursionError):
        return None
    return found


def _result_from_events(raw):
    thread_id = None
    final = None
    usage = None
    tool_calls = 0
    tool_calls_recognized = False
    first = True
    try:
        for line in raw.decode('utf-8').splitlines():
            if not line:
                continue
            event = json.loads(line, object_pairs_hook=_unique_keys,
                                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(event) is not dict:
                _fail('CODEX_EVENT_INVALID')
            if first and event.get('type') != 'thread.started':
                _fail('CODEX_THREAD_ID_MISSING')
            first = False
            if event.get('type') == 'thread.started':
                current = event.get('thread_id')
                if type(current) is not str or not _THREAD_ID.fullmatch(current) or (thread_id and current != thread_id):
                    _fail('CODEX_THREAD_ID_INVALID')
                thread_id = current
            elif event.get('type') == 'item.started':
                item = event.get('item')
                if type(item) is dict and item.get('type') in _TOOL_CALL_ITEM_TYPES:
                    tool_calls += 1
                    tool_calls_recognized = True
            elif event.get('type') == 'item.completed':
                item = event.get('item')
                if type(item) is dict and item.get('type') == 'agent_message':
                    if type(item.get('text')) is not str:
                        _fail('CODEX_FINAL_MESSAGE_INVALID')
                    final = item['text']
            elif event.get('type') == 'turn.completed':
                candidate = event.get('usage')
                if type(candidate) is dict:
                    prompt_tokens = candidate.get('input_tokens')
                    completion_tokens = candidate.get('output_tokens')
                    if (type(prompt_tokens) is int and prompt_tokens >= 0 and
                            type(completion_tokens) is int and completion_tokens >= 0):
                        usage = {'prompt_tokens': prompt_tokens,
                                 'completion_tokens': completion_tokens,
                                 'total_tokens': prompt_tokens + completion_tokens}
                        cached_tokens = candidate.get('cached_input_tokens')
                        if type(cached_tokens) is int and cached_tokens >= 0:
                            usage['cached_prompt_tokens'] = cached_tokens
                        reasoning_tokens = candidate.get('reasoning_output_tokens')
                        if type(reasoning_tokens) is int and reasoning_tokens >= 0:
                            usage['reasoning_tokens'] = reasoning_tokens
    except DirectCodexError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        _fail('CODEX_EVENT_INVALID')
    if usage is not None and tool_calls_recognized:
        usage['tool_calls'] = tool_calls
    if thread_id is None:
        _fail('CODEX_THREAD_ID_MISSING')
    if final is None:
        _fail('CODEX_FINAL_MESSAGE_MISSING')
    try:
        obj = json.loads(final, object_pairs_hook=_unique_keys,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, RecursionError):
        _fail('CODEX_FINAL_JSON_INVALID')
    fields = {'status', 'summary', 'tests', 'self_review', 'blocked_reason'}
    try:
        invalid = (type(obj) is not dict or set(obj) != fields or
            obj.get('status') not in ('DONE', 'BLOCKED') or
            type(obj.get('summary')) is not str or len(obj['summary'].encode('utf-8')) > 8192 or
            type(obj.get('tests')) is not list or len(obj['tests']) > 32 or
            any(type(item) is not str or len(item.encode('utf-8')) > 1024 for item in obj['tests']) or
            type(obj.get('self_review')) is not str or len(obj['self_review'].encode('utf-8')) > 8192 or
            (obj.get('blocked_reason') is not None and
             (type(obj['blocked_reason']) is not str or len(obj['blocked_reason'].encode('utf-8')) > 2048)))
    except (UnicodeError, TypeError, KeyError):
        invalid = True
    if invalid:
        _fail('CODEX_FINAL_JSON_INVALID')
    if (obj['status'] == 'BLOCKED') != (obj['blocked_reason'] is not None):
        _fail('CODEX_FINAL_JSON_INVALID')
    return thread_id, obj, usage


def _usage_from_events(raw):
    """Retain valid usage observations even when the CLI turn times out."""
    usage = None
    tool_calls = 0
    tool_calls_recognized = False
    try:
        for line in raw.decode('utf-8').splitlines():
            if not line:
                continue
            event = json.loads(line, object_pairs_hook=_unique_keys,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(event) is not dict or event.get('type') != 'turn.completed':
                item = event.get('item') if type(event) is dict else None
                if (type(event) is dict and event.get('type') == 'item.started' and
                        type(item) is dict and item.get('type') in _TOOL_CALL_ITEM_TYPES):
                    tool_calls += 1
                    tool_calls_recognized = True
                continue
            candidate = event.get('usage')
            if type(candidate) is not dict:
                continue
            prompt_tokens = candidate.get('input_tokens')
            completion_tokens = candidate.get('output_tokens')
            if (type(prompt_tokens) is int and prompt_tokens >= 0 and
                    type(completion_tokens) is int and completion_tokens >= 0):
                usage = {'prompt_tokens': prompt_tokens,
                         'completion_tokens': completion_tokens,
                         'total_tokens': prompt_tokens + completion_tokens}
                cached_tokens = candidate.get('cached_input_tokens')
                if type(cached_tokens) is int and cached_tokens >= 0:
                    usage['cached_prompt_tokens'] = cached_tokens
                reasoning_tokens = candidate.get('reasoning_output_tokens')
                if type(reasoning_tokens) is int and reasoning_tokens >= 0:
                    usage['reasoning_tokens'] = reasoning_tokens
    except (UnicodeError, ValueError, RecursionError):
        return usage
    if usage is not None and tool_calls_recognized:
        usage['tool_calls'] = tool_calls
    return usage


def _has_valid_final_result(raw):
    try:
        _result_from_events(raw)
    except DirectCodexError:
        return False
    return True


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _environment(tmpdir):
    env = {name: os.environ[name] for name in (
        'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE',
        'CODEX_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if name in os.environ}
    for name in ('HOME', 'CODEX_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR'):
        if name in env and (not os.path.isabs(env[name]) or any(ord(c) < 32 for c in env[name])):
            _fail('CODEX_ENV_INVALID')
    env['TMPDIR'] = tmpdir
    return env


def validate_profile(model, effort):
    if (type(model) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', model) or
            type(effort) is not str or not _EFFORT.fullmatch(effort)):
        _fail('CODEX_PROFILE_INVALID')
    return model, effort


def _argv(executable, root, model, effort, *, pycache_prefix, command_path=None):
    model, effort = validate_profile(model, effort)
    if type(pycache_prefix) is not str or not os.path.isabs(pycache_prefix):
        _fail('CODEX_ENV_INVALID')
    command_path = _command_path(root) if command_path is None else command_path
    if (type(command_path) is not str or not command_path or
            any(not os.path.isabs(entry) or any(ord(c) < 32 for c in entry)
                for entry in command_path.split(':'))):
        _fail('CODEX_ENV_INVALID')
    args = [executable, '--no-daemon', '--ask-for-approval', 'never', 'exec',
            '--ignore-user-config', '--ignore-rules', '--ephemeral', '--strict-config',
            '--color', 'never', '--json', '--model', model,
            '-c', 'model_reasoning_effort=' + effort,
            '--sandbox', 'workspace-write', '--cd', str(root), '-']
    settings = (
        'web_search="disabled"', 'features.multi_agent=false', 'features.hooks=false',
        'features.apps=false', 'features.memories=false', 'features.remote_plugin=false',
        'features.code_mode.enabled=false', 'shell_environment_policy.inherit="none"',
        'shell_environment_policy.set.PYTHONDONTWRITEBYTECODE="1"',
        'shell_environment_policy.set.PYTHONPYCACHEPREFIX=' + json.dumps(pycache_prefix),
        'shell_environment_policy.set.PATH=' + json.dumps(command_path),
        'allow_login_shell=false', 'model_reasoning_summary="none"',
        'hide_agent_reasoning=true', 'show_raw_agent_reasoning=false',
    )
    for setting in settings:
        args.extend(('-c', setting))
    return args


def run(*, root, prompt, model, reasoning_effort, dispatch_store, dispatch_task_id,
        prelaunch_validate=None, timeout=MAX_WALL_CLOCK_SECONDS):
    root = Path(root).resolve(strict=True)
    if type(prompt) is not str or len(prompt.encode('utf-8')) > MAX_PROMPT_BYTES:
        _fail('CODEX_PROMPT_INVALID')
    if type(timeout) not in (int, float) or not 0 < timeout <= MAX_WALL_CLOCK_SECONDS:
        _fail('CODEX_BUDGET_INVALID')
    executable = shutil.which('codex')
    if executable is None or not os.path.isabs(executable):
        _fail('CODEX_RUNTIME_UNAVAILABLE')
    started = time.monotonic()
    deadline = started + timeout
    output, errors = bytearray(), bytearray()
    process = None
    lifecycle = CLIProcessLifecycle('codex-direct-developer', deadline=deadline + 5)
    lifecycle.__enter__()
    lifecycle_closed = False

    def close_lifecycle(kind=None, value=None, traceback=None):
        nonlocal lifecycle_closed
        if not lifecycle_closed:
            lifecycle_closed = True
            lifecycle.__exit__(kind, value, traceback)

    try:
        with tempfile.TemporaryDirectory(prefix='llmtool-codex-direct-') as tmpdir:
            os.chmod(tmpdir, 0o700)
            env = _environment(tmpdir)
            try:
                if prelaunch_validate is not None:
                    try:
                        valid = prelaunch_validate()
                    except DirectCodexError:
                        raise
                    except Exception:
                        valid = False
                    if valid is not True:
                        _fail('CODEX_D6_BINDING_CHANGED')
                try:
                    worker_dispatch.authorize_dispatch(
                        dispatch_store, dispatch_task_id, 'codex-developer', 'DISPATCH')
                except worker_dispatch.WorkerDispatchDenied as exc:
                    raise DirectCodexError(exc.dispatch_code) from None
                try:
                    process = subprocess.Popen(
                        _argv(executable, root, model, reasoning_effort,
                              pycache_prefix=tmpdir), cwd=str(root), env=env,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        bufsize=0, shell=False, close_fds=True, start_new_session=True)
                except OSError:
                    _fail('CODEX_RUNTIME_UNAVAILABLE')
                try:
                    lifecycle.own(process)
                except LLMToolError:
                    _fail('CODEX_PROCESS_OWNERSHIP_UNKNOWN')
                selector = selectors.DefaultSelector()
                try:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        os.set_blocking(stream.fileno(), False)
                    selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
                    selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
                    selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
                    payload = prompt.encode('utf-8')
                    offset = 0
                    while selector.get_map():
                        lifecycle.check()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            timeout_error = DirectCodexError('CODEX_TIMEOUT')
                            timeout_error.usage = _usage_from_events(bytes(output))
                            raise timeout_error
                        for key, _ in selector.select(min(remaining, 0.1)):
                            if key.data == 'stdin':
                                try:
                                    if offset < len(payload):
                                        offset += os.write(key.fileobj.fileno(), payload[offset:offset + 8192])
                                    else:
                                        selector.unregister(key.fileobj)
                                        key.fileobj.close()
                                except (BrokenPipeError, OSError):
                                    selector.unregister(key.fileobj)
                                    key.fileobj.close()
                            else:
                                chunk = os.read(key.fileobj.fileno(), 8192)
                                if not chunk:
                                    selector.unregister(key.fileobj)
                                    continue
                                target, cap = ((output, MAX_OUTPUT_BYTES) if key.data == 'stdout'
                                               else (errors, MAX_STDERR_BYTES))
                                if len(target) + len(chunk) > cap:
                                    _fail('CODEX_OUTPUT_TOO_LARGE' if key.data == 'stdout'
                                          else 'CODEX_STDERR_TOO_LARGE')
                                target.extend(chunk)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timeout_error = DirectCodexError('CODEX_TIMEOUT')
                        timeout_error.usage = _usage_from_events(bytes(output))
                        raise timeout_error
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timeout_error = DirectCodexError('CODEX_TIMEOUT')
                    timeout_error.usage = _usage_from_events(bytes(output))
                    raise timeout_error from None
                finally:
                    selector.close()
                    for stream in (process.stdin, process.stdout, process.stderr):
                        try:
                            stream.close()
                        except OSError:
                            pass
            except BaseException as exc:
                # Keep TMPDIR alive until the owned child has stopped using it.
                close_lifecycle(type(exc), exc, exc.__traceback__)
                raise
        # A signalled child may have emitted no JSON, or only a partial JSONL
        # record. Preserve the more useful interruption outcome before any
        # event/final-message parser can classify that output as malformed.
        if process.returncode is not None and process.returncode < 0:
            _fail('CODEX_INTERRUPTED')
        if process.returncode != 0:
            _fail('CODEX_EXIT_NONZERO')
        thread_id, result, usage = _result_from_events(bytes(output))
        close_lifecycle()
        cleanup_status = 'CONFIRMED' if lifecycle.cleanup_confirmed else 'UNKNOWN'
        return DirectResult(result['status'], result['summary'], tuple(result['tests']),
                            result['self_review'], result['blocked_reason'], process.returncode,
                            thread_id, hashlib.sha256(output).hexdigest(),
                            max(0, int((time.monotonic() - started) * 1000)),
                            cleanup_status, usage)
    except Exception as exc:
        try:
            close_lifecycle(type(exc), exc, exc.__traceback__)
        except BaseException as interrupt:
            # A host signal surfaces from cleanup: keep the observed facts on it.
            _attach_observed(interrupt, process, output, started, lifecycle)
            raise
        if isinstance(exc, CLIHostTermination):
            _attach_observed(exc, process, output, started, lifecycle)
            raise
        # An unexpected exception still becomes a typed, fail-closed result
        # with the observed process facts, never a bare adapter UNKNOWN.
        error = (exc if isinstance(exc, DirectCodexError) else
                 DirectCodexError(adapter_error_code(exc)))
        if (process is not None and process.returncode is not None and
                process.returncode < 0 and
                error.direct_codex_code != 'CODEX_TIMEOUT' and
                not _has_valid_final_result(bytes(output))):
            error.direct_codex_code = 'CODEX_INTERRUPTED'
        error.attempted = process is not None
        error.process_exit_code = process.returncode if process is not None else None
        error.stdout_sha256 = hashlib.sha256(output).hexdigest() if output else None
        error.elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        error.cleanup_status = 'CONFIRMED' if lifecycle.cleanup_confirmed else 'UNKNOWN'
        error.native_session_id = _thread_from_events(bytes(output))
        if error.usage is None:
            error.usage = _usage_from_events(bytes(output))
        if error is exc:
            raise
        raise error from None
    except BaseException as exc:
        close_lifecycle(type(exc), exc, exc.__traceback__)
        _attach_observed(exc, process, output, started, lifecycle)
        raise


def _attach_observed(exc, process, output, started, lifecycle):
    try:
        exc._llmtool_direct_codex_execution = {
            'native_session_id': _thread_from_events(bytes(output)),
            'process_exit_code': process.returncode if process is not None else None,
            'stdout_sha256': hashlib.sha256(output).hexdigest() if output else None,
            'elapsed_ms': max(0, int((time.monotonic() - started) * 1000)),
            'cleanup_status': 'CONFIRMED' if lifecycle.cleanup_confirmed else 'UNKNOWN',
        }
    except Exception:
        pass

