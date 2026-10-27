"""Bounded, single-turn AGY CLI Developer invocation for a registered D6 task."""

from __future__ import annotations

from dataclasses import dataclass
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

from . import task_completion, worker_dispatch
from .errors import LLMToolError
from .providers.cli_lifecycle import CLIHostTermination, CLIProcessLifecycle

MAX_WALL_CLOCK_SECONDS = 600
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_PROMPT_BYTES = 64 * 1024
MAX_LOG_BYTES = 4 * 1024 * 1024
_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z')
_EFFORT = re.compile(r'(?:minimal|low|medium|high)\Z')
_CONVERSATION = re.compile(r'[A-Za-z0-9_-]{8,128}\Z')


class DirectAgyError(LLMToolError):
    """Content-free AGY failure with bounded execution observations."""

    def __init__(self, agy_code, *, attempted=False, process_exit_code=None,
                 stdout_sha256=None, elapsed_ms=None, cleanup_status='UNKNOWN',
                 conversation_id=None, usage=None, progress=None,
                 changed_files=None, reason_code=None):
        super().__init__('AGY Developer execution unavailable; details omitted')
        self.agy_code = agy_code
        self.attempted = attempted
        self.process_exit_code = process_exit_code
        self.stdout_sha256 = stdout_sha256
        self.elapsed_ms = elapsed_ms
        self.cleanup_status = cleanup_status
        self.conversation_id = conversation_id
        self.usage = usage
        self.progress = progress
        self.changed_files = changed_files
        self.reason_code = reason_code


@dataclass(frozen=True)
class AgyResult:
    status: str
    summary: str
    conversation_id: str
    model: str
    effort: str
    usage: dict | None
    changed_files: tuple
    exit_code: int
    stdout_sha256: str
    elapsed_ms: int
    cleanup_status: str


def _fail(code, **facts):
    raise DirectAgyError(code, **facts)


def validate_profile(model, effort):
    if (type(model) is not str or _MODEL.fullmatch(model) is None or
            type(effort) is not str or _EFFORT.fullmatch(effort) is None):
        _fail('AGY_PROFILE_INVALID')
    return model, effort


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _json(raw):
    try:
        obj = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _fail('AGY_OUTPUT_INVALID')
    if type(obj) is not dict:
        _fail('AGY_OUTPUT_INVALID')
    return obj


def _usage(obj):
    value = obj.get('usage')
    if type(value) is not dict:
        return None
    clean = {key: item for key, item in value.items()
             if type(key) is str and type(item) is int}
    return clean or None


def _quota(*values):
    return any(type(value) is str and re.search(
        r'quota|RESOURCE_EXHAUSTED|rate\s*limit', value, re.IGNORECASE)
        for value in values)


def _git_state(root):
    common = task_completion._git(root, ('rev-parse', '--path-format=absolute', '--git-common-dir'),
                                  limit=4096).decode('utf-8', 'strict').strip()
    gitdir = task_completion._git(root, ('rev-parse', '--path-format=absolute', '--git-dir'),
                                  limit=4096).decode('utf-8', 'strict').strip()
    refs = task_completion._git(root, ('for-each-ref', '--format=%(refname) %(objectname)',
                                       'refs'), limit=1024 * 1024)
    head = task_completion._git(root, ('rev-parse', '--verify', 'HEAD'), limit=4096).decode().strip()
    branch = task_completion._git(root, ('branch', '--show-current'), limit=4096).decode().strip()
    index = task_completion._git(root, ('ls-files', '--stage', '-z'), limit=1024 * 1024)
    pointer = Path(root) / '.git'
    if pointer.is_symlink():
        pointer_data = ('symlink', os.readlink(pointer))
    elif pointer.is_file():
        pointer_data = ('file', pointer.read_bytes())
    elif pointer.is_dir():
        pointer_data = ('directory',)
    else:
        pointer_data = ('absent',)
    try:
        git_head = (Path(gitdir) / 'HEAD').read_bytes()
    except OSError:
        _fail('AGY_GIT_INSPECTION_FAILED')
    return ((head, branch, index, refs, git_head, os.path.realpath(common)), pointer_data)


def _tree_snapshot(root):
    """Fingerprint all ordinary files and symlinks below root, excluding Git internals."""
    found = {}
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        relbase = os.path.relpath(base, root)
        if relbase == '.':
            dirs[:] = [name for name in dirs if name != '.git']
        for name in list(dirs):
            path = Path(base) / name
            if path.is_symlink():
                rel = os.path.relpath(path, root).replace(os.sep, '/')
                found[rel] = ('symlink', os.readlink(path))
                dirs.remove(name)
        for name in files:
            path = Path(base) / name
            rel = os.path.relpath(path, root).replace(os.sep, '/')
            try:
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    found[rel] = ('symlink', os.readlink(path))
                elif stat.S_ISREG(info.st_mode):
                    found[rel] = (stat.S_IMODE(info.st_mode),
                                  hashlib.sha256(path.read_bytes()).hexdigest())
                else:
                    found[rel] = ('unsupported', stat.S_IFMT(info.st_mode))
            except OSError:
                found[rel] = ('absent',)
    return found


def _snapshot(root):
    return _tree_snapshot(root), _git_state(root)


def _changed(before, after):
    a, b = before[0], after[0]
    paths = sorted(path for path in set(a) | set(b) if a.get(path) != b.get(path))
    return paths, before[1][0] != after[1][0], before[1][1] != after[1][1]


def _environment(tmpdir):
    env = {name: os.environ[name] for name in (
        'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE',
        'AGY_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if name in os.environ}
    for name in ('HOME', 'AGY_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR'):
        if name in env and (not os.path.isabs(env[name]) or
                            any(ord(char) < 32 for char in env[name])):
            _fail('AGY_ENV_INVALID')
    env['TMPDIR'] = tmpdir
    return env


def _argv(executable, prompt, model, effort, log_path):
    return [executable, '-p', prompt, '--model', model, '--effort', effort,
            '--sandbox', '--output-format', 'json', '--print-timeout', '0',
            '--log-file', str(log_path)]


def _default_runner(argv, cwd, env, timeout):
    started = time.monotonic()
    deadline = started + timeout
    lifecycle = CLIProcessLifecycle('agy-direct-developer', deadline=deadline + 5)
    lifecycle.__enter__()
    process = None
    out, err = bytearray(), bytearray()
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   shell=False, close_fds=True, start_new_session=True)
        lifecycle.own(process)
        selector = selectors.DefaultSelector()
        try:
            for stream, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                lifecycle.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError from None
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target, cap = ((out, MAX_OUTPUT_BYTES) if key.data == 'stdout'
                                   else (err, MAX_STDERR_BYTES))
                    if len(target) + len(chunk) > cap:
                        _fail('AGY_OUTPUT_TOO_LARGE' if key.data == 'stdout'
                              else 'AGY_STDERR_TOO_LARGE')
                    target.extend(chunk)
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError from None
        process.wait(timeout=remaining)
        return process.returncode, bytes(out), bytes(err)
    finally:
        try:
            lifecycle.__exit__(None, None, None)
        finally:
            _default_runner.last_cleanup_status = (
                'CONFIRMED' if lifecycle.cleanup_confirmed else 'UNKNOWN')
            _default_runner.last_facts = {
                'process_exit_code': process.returncode if process is not None else None,
                'stdout': bytes(out), 'stderr': bytes(err),
            }


_default_runner.last_cleanup_status = 'UNKNOWN'
_default_runner.last_facts = {'process_exit_code': None, 'stdout': b'', 'stderr': b''}


def run(*, root, task_id, owner_ref, prompt, model, effort, expected_branch,
        expected_head, write_paths, log_path, runner=None, write_boundary=None,
        timeout=MAX_WALL_CLOCK_SECONDS):
    started = time.monotonic()
    output = b''
    errors = b''
    process_exit_code = None
    conversation_id = None
    usage = None
    cleanup_status = 'UNKNOWN'
    attempted = False
    try:
        root = Path(root).resolve(strict=True)
        validate_profile(model, effort)
        if type(prompt) is not str:
            _fail('AGY_PROMPT_INVALID')
        try:
            if len(prompt.encode('utf-8')) > MAX_PROMPT_BYTES:
                _fail('AGY_PROMPT_INVALID')
        except UnicodeError:
            _fail('AGY_PROMPT_INVALID')
        log_path = Path(log_path)
        if not log_path.is_absolute() or root == log_path.resolve(strict=False) or root in log_path.resolve(strict=False).parents:
            _fail('AGY_LOG_PATH_INVALID')
        if (type(timeout) not in (int, float) or not 0 < timeout <= MAX_WALL_CLOCK_SECONDS):
            _fail('AGY_PROFILE_INVALID')
        decision = worker_dispatch.evaluate('agy', owner_ref, task_id)
        if not decision.allowed or decision.reason_code != 'OWNER_ASSIGNMENT_MATCHED':
            _fail('AGY_OWNER_ASSIGNMENT_REQUIRED', reason_code=decision.reason_code)
        try:
            before_git = task_completion.collect_git_snapshot(root)
        except Exception:
            _fail('AGY_D6_BINDING_INVALID')
        if before_git.branch != expected_branch or before_git.head != expected_head:
            _fail('AGY_D6_BINDING_INVALID')
        if not before_git.clean:
            _fail('AGY_D6_DIRTY')
        if not callable(write_boundary):
            _fail('AGY_WRITE_ISOLATION_UNVERIFIED')
        try:
            boundary = write_boundary()
        except Exception:
            _fail('AGY_WRITE_ISOLATION_UNVERIFIED')
        if (type(boundary) is not dict or boundary.get('verified') is not True or
                type(boundary.get('policy_sha256')) is not str or
                re.fullmatch(r'[0-9a-f]{64}', boundary['policy_sha256']) is None):
            _fail('AGY_WRITE_ISOLATION_UNVERIFIED')
        if type(write_paths) not in (list, tuple) or any(type(p) is not str for p in write_paths):
            _fail('AGY_WRITE_ISOLATION_UNVERIFIED')
        allowed = set(write_paths)
        if any(not p or p.startswith('/') or '\\' in p or
               any(part in ('', '.', '..') for part in p.split('/')) for p in allowed):
            _fail('AGY_WRITE_ISOLATION_UNVERIFIED')
        before = _snapshot(root)
        executable = shutil.which('agy')
        if executable is None or not os.path.isabs(executable):
            _fail('AGY_RUNTIME_UNAVAILABLE')
        with tempfile.TemporaryDirectory(prefix='llmtool-agy-direct-') as tmpdir:
            os.chmod(tmpdir, 0o700)
            env = _environment(tmpdir)
            args = _argv(executable, prompt, model, effort, str(log_path))
            attempted = True
            try:
                _default_runner.last_facts = {
                    'process_exit_code': None, 'stdout': b'', 'stderr': b''}
                code, output, errors = (runner or _default_runner)(args, str(root), env, timeout)
            except (TimeoutError, subprocess.TimeoutExpired):
                if runner is None:
                    cleanup_status = _default_runner.last_cleanup_status
                    facts = _default_runner.last_facts
                    process_exit_code = facts['process_exit_code']
                    output, errors = facts['stdout'], facts['stderr']
                _fail('AGY_TIMEOUT')
            except CLIHostTermination:
                raise
            except Exception as exc:
                if runner is None:
                    cleanup_status = _default_runner.last_cleanup_status
                    facts = _default_runner.last_facts
                    process_exit_code = facts['process_exit_code']
                    output, errors = facts['stdout'], facts['stderr']
                if isinstance(exc, DirectAgyError):
                    raise
                _fail('AGY_ADAPTER_' + re.sub(r'[^A-Z0-9_]', '_', type(exc).__name__.upper())[:48])
            if runner is None:
                cleanup_status = _default_runner.last_cleanup_status
            if type(code) is not int or type(output) is not bytes or type(errors) is not bytes:
                _fail('AGY_ADAPTER_RUNNER_RESULT_INVALID')
            if len(output) > MAX_OUTPUT_BYTES:
                _fail('AGY_OUTPUT_TOO_LARGE')
            if len(errors) > MAX_STDERR_BYTES:
                _fail('AGY_STDERR_TOO_LARGE')
            process_exit_code = code
            obj = None
            try:
                obj = _json(output)
                raw_id = obj.get('conversation_id')
                if type(raw_id) is str and _CONVERSATION.fullmatch(raw_id):
                    conversation_id = raw_id
                usage = _usage(obj)
            except DirectAgyError:
                if not _quota(errors.decode('utf-8', 'replace')):
                    raise
            if _quota((obj or {}).get('status') if obj else None,
                      (obj or {}).get('error') if obj else None,
                      errors.decode('utf-8', 'replace')):
                post = _snapshot(root)
                changed, _, _ = _changed(before, post)
                changed = changed[:200]
                progress = {'state': 'QUOTA_EXHAUSTED',
                            'head_sha': post[1][0][0] or 'UNKNOWN',
                            'changed_files': changed, 'unfinished_checks': ['UNKNOWN'],
                            'conversation_id': conversation_id}
                _fail('AGY_QUOTA_EXHAUSTED', progress=progress,
                      conversation_id=conversation_id, usage=usage, changed_files=changed)
            after = _snapshot(root)
            changed, git_changed, pointer_changed = _changed(before, after)
            reported = changed[:200]
            if pointer_changed:
                _fail('AGY_WRITE_SCOPE_VIOLATION', changed_files=reported)
            if git_changed:
                _fail('AGY_GIT_STATE_CHANGED', changed_files=reported)
            if any(path not in allowed or before[0].get(path, (None,))[0] == 'symlink' or
                   after[0].get(path, (None,))[0] == 'symlink' for path in changed):
                _fail('AGY_WRITE_SCOPE_VIOLATION', changed_files=reported)
            if code < 0:
                _fail('AGY_INTERRUPTED')
            if code != 0:
                _fail('AGY_EXIT_NONZERO')
            if obj is None:
                obj = _json(output)
            if (type(obj.get('conversation_id')) is not str or
                    _CONVERSATION.fullmatch(obj['conversation_id']) is None or
                    type(obj.get('status')) is not str):
                _fail('AGY_OUTPUT_INVALID')
            conversation_id = obj['conversation_id']
            try:
                log = log_path.read_bytes()
            except OSError:
                log = b''
            if len(log) > MAX_LOG_BYTES:
                _fail('AGY_MODEL_UNVERIFIED')
            try:
                log_text = log.decode('utf-8')
            except UnicodeError:
                _fail('AGY_MODEL_UNVERIFIED')
            resolved = re.findall(r'model_resolver.*?Resolving model ([^\s]+)', log_text)
            if not resolved:
                _fail('AGY_MODEL_UNVERIFIED')
            if any(name != model for name in resolved):
                _fail('AGY_MODEL_MISMATCH')
            if runner is not None:
                cleanup_status = 'CONFIRMED'
            summary = obj.get('response', obj.get('result', ''))
            if type(summary) is not str:
                summary = ''
            return AgyResult(obj['status'], summary[:4000], conversation_id, model, effort,
                             usage, tuple(path for path in changed if path in allowed), code,
                             hashlib.sha256(output).hexdigest(),
                             max(0, int((time.monotonic()-started)*1000)), cleanup_status)
    except Exception as exc:
        if isinstance(exc, DirectAgyError):
            error = exc
        else:
            error = DirectAgyError('AGY_ADAPTER_' +
                re.sub(r'[^A-Z0-9_]', '_', type(exc).__name__.upper())[:48])
        error.attempted = attempted
        error.process_exit_code = process_exit_code
        error.stdout_sha256 = hashlib.sha256(output).hexdigest() if output else None
        error.elapsed_ms = max(0, int((time.monotonic()-started)*1000))
        error.cleanup_status = cleanup_status
        if error.conversation_id is None:
            error.conversation_id = conversation_id
        if error.usage is None:
            error.usage = usage
        raise error from None
