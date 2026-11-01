"""Shared, fail-closed contract for direct CLI Developer routes.

Provider-specific facts are supplied by a route configuration. This module
never embeds provider paths, account names, hosts, or output content in errors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
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
from typing import Any, Mapping

from ..errors import LLMToolError
from ..providers.cli_lifecycle import CLIProcessLifecycle
from .. import task_completion

MAX_STDOUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_TIMEOUT_SECONDS = 600


class RouteError(LLMToolError):
    """Fixed-text route failure with bounded, non-content observations."""

    def __init__(self, code: str, *, blocked_reason: str | None = None,
                 progress: Mapping[str, Any] | None = None,
                 facts: Mapping[str, Any] | None = None):
        if type(code) is not str or re.fullmatch(r'[A-Z0-9_]{1,64}', code) is None:
            code = 'ROUTE_ERROR'
        super().__init__('Developer route execution unavailable; details omitted')
        self.code = code
        self.blocked_reason = blocked_reason
        self.progress = dict(progress) if progress is not None else None
        self.observed_facts = dict(facts or {})
        self.facts = self.observed_facts


@dataclass(frozen=True)
class RouteResult:
    route: str
    status: str
    summary: str
    session_id: str | None
    model: str
    effort: str
    usage: Mapping[str, Any] | None
    changed_files: tuple[str, ...]
    exit_code: int
    stdout_sha256: str
    elapsed_ms: int
    cleanup_status: str
    boundary: Mapping[str, Any]


class DeveloperRoute(ABC):
    """Provider adapter contract. ``config`` contains all provider facts."""

    def __init__(self, config: Mapping[str, Any]):
        if not isinstance(config, Mapping):
            raise TypeError('route config must be a mapping')
        self.config = dict(config)

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def worker_key(self) -> str: ...

    @property
    @abstractmethod
    def family(self) -> str: ...

    @abstractmethod
    def validate_profile(self, model: str, effort: str) -> tuple[str, str]: ...

    @abstractmethod
    def build_argv(self, executable: str, request: Mapping[str, Any]) -> list[str]: ...

    @abstractmethod
    def build_env(self, tmpdir: str) -> dict[str, str]: ...

    @abstractmethod
    def parse_output(self, stdout: bytes, stderr: bytes,
                     log_text: str) -> Mapping[str, Any]: ...

    @abstractmethod
    def verified_model(self, log_text: str, requested: str) -> str | None: ...

    @abstractmethod
    def quota_exhausted(self, parsed: Mapping[str, Any], stderr: bytes) -> bool: ...

    def validate_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise RouteError(f'{self.name.upper()}_REQUEST_INVALID')
        value = dict(request)
        required = ('root', 'model', 'effort', 'expected_branch', 'expected_head',
                    'write_paths', 'executable')
        if any(key not in value for key in required):
            raise RouteError(f'{self.name.upper()}_REQUEST_INVALID')
        try:
            root = Path(value['root']).resolve(strict=True)
        except (TypeError, OSError, RuntimeError):
            raise RouteError(f'{self.name.upper()}_REQUEST_INVALID') from None
        if not root.is_dir():
            raise RouteError(f'{self.name.upper()}_REQUEST_INVALID')
        model, effort = self.validate_profile(value['model'], value['effort'])
        executable = value['executable']
        if type(executable) is not str or not os.path.isabs(executable):
            raise RouteError(f'{self.name.upper()}_REQUEST_INVALID')
        paths = value['write_paths']
        if (type(paths) not in (tuple, list) or
                any(type(path) is not str or not path or path.startswith('/') or
                    '\\' in path or any(part in ('', '.', '..') for part in path.split('/'))
                    for path in paths)):
            raise RouteError(f'{self.name.upper()}_REQUEST_INVALID')
        branch, head = value['expected_branch'], value['expected_head']
        if (type(branch) is not str or not branch or type(head) is not str or
                re.fullmatch(r'[0-9a-f]{40}', head) is None):
            raise RouteError(f'{self.name.upper()}_REQUEST_INVALID')
        timeout = value.get('timeout', MAX_TIMEOUT_SECONDS)
        if type(timeout) not in (int, float) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
            raise RouteError(f'{self.name.upper()}_REQUEST_INVALID')
        value.update(root=str(root), model=model, effort=effort, timeout=timeout,
                     write_paths=tuple(paths))
        return value


def _default_runner(argv, cwd, env, timeout):
    started = time.monotonic()
    deadline = started + timeout
    lifecycle = CLIProcessLifecycle('direct-developer-route', deadline=deadline + 5)
    out, err = bytearray(), bytearray()
    process = None
    lifecycle.__enter__()
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
                    else:
                        target, cap = ((out, MAX_STDOUT_BYTES) if key.data == 'stdout'
                                       else (err, MAX_STDERR_BYTES))
                        if len(target) + len(chunk) > cap:
                            raise RouteError('ROUTE_OUTPUT_TOO_LARGE')
                        target.extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError from None
            process.wait(timeout=remaining)
            return process.returncode, bytes(out), bytes(err)
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
    finally:
        lifecycle.__exit__(None, None, None)
        _default_runner.last_cleanup_status = 'CONFIRMED' if lifecycle.cleanup_confirmed else 'UNKNOWN'


_default_runner.last_cleanup_status = 'UNKNOWN'


def _route_code(route, suffix):
    return route.name.upper() + '_' + suffix


def _snapshot_value(value):
    """Canonicalize snapshots while retaining only diagnostic-safe file names."""
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, '__dict__'):
        return dict(vars(value))
    raise RouteError('ROUTE_SNAPSHOT_INVALID')


def capture_snapshot(root):
    """Fingerprint worktree files and read-only Git identity/state."""
    root = Path(root).resolve(strict=True)
    files = {}
    for base, dirs, names in os.walk(root, topdown=True, followlinks=False):
        if Path(base) == root:
            dirs[:] = [name for name in dirs if name != '.git']
        for name in list(dirs):
            path = Path(base) / name
            if path.is_symlink():
                rel = path.relative_to(root).as_posix()
                files[rel] = ('symlink', os.readlink(path))
                dirs.remove(name)
        for name in names:
            path = Path(base) / name
            rel = path.relative_to(root).as_posix()
            try:
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    files[rel] = ('symlink', os.readlink(path))
                elif stat.S_ISREG(info.st_mode):
                    files[rel] = (stat.S_IMODE(info.st_mode),
                                  hashlib.sha256(path.read_bytes()).hexdigest())
                else:
                    files[rel] = ('unsupported', stat.S_IFMT(info.st_mode))
            except OSError:
                files[rel] = ('unavailable',)
    branch = task_completion._git(root, ('branch', '--show-current'), limit=4096).decode().strip()
    branch_ref = (task_completion._git(
        root, ('rev-parse', '--verify', f'refs/heads/{branch}'), limit=4096).decode().strip()
                  if branch else '')
    git_dir = Path(task_completion._git(
        root, ('rev-parse', '--path-format=absolute', '--git-dir'),
        limit=4096).decode().strip())
    common_dir = Path(task_completion._git(
        root, ('rev-parse', '--path-format=absolute', '--git-common-dir'),
        limit=4096).decode().strip())
    refs_raw = task_completion._git(
        root, ('for-each-ref', '--format=%(refname) %(objectname)'), limit=1024 * 1024)
    refs = {}
    for line in refs_raw.decode().splitlines():
        name, object_id = line.split(' ', 1)
        refs[name] = object_id
    # Keep an object inventory so newly-created tag and notes objects are
    # distinguishable from refs moved to objects that already existed.
    objects_raw = task_completion._git(
        root, ('cat-file', '--batch-all-objects', '--batch-check=%(objectname)'),
        limit=1024 * 1024)
    objects = frozenset(objects_raw.decode().splitlines())
    head = task_completion._git(root, ('rev-parse', '--verify', 'HEAD'), limit=4096).decode().strip()
    head_commits = frozenset(task_completion._git(
        root, ('rev-list', '--topo-order', 'HEAD'), limit=1024 * 1024).decode().splitlines())

    def file_digest(path):
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return None

    git = {
        'head': head,
        'branch': branch,
        'branch_ref': branch_ref,
        'git_dir': str(git_dir),
        'index': task_completion._git(root, ('ls-files', '--stage', '-z'), limit=1024 * 1024),
        'stash': task_completion._git(root, ('for-each-ref', '--format=%(objectname)',
                                             'refs/stash'), limit=4096).strip(),
        'refs': refs,
        'objects': objects,
        'head_commits': head_commits,
        'config_worktree': file_digest(git_dir / 'config.worktree'),
        'config_common': file_digest(common_dir / 'config'),
    }
    pointer = root / '.git'
    if pointer.is_symlink():
        git['pointer'] = ('symlink', os.readlink(pointer))
    elif pointer.is_file():
        git['pointer'] = ('file', pointer.read_bytes())
    else:
        git['pointer'] = ('directory',)
    return {'files': files, 'git_state': git}


def _changed_files(before: Any, after: Any) -> tuple[str, ...]:
    """Compare tree maps when supplied; generic snapshots may expose ``files``."""
    left, right = _snapshot_value(before), _snapshot_value(after)
    a, b = left.get('files', {}), right.get('files', {})
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return tuple(sorted(str(key) for key in set(a) | set(b) if a.get(key) != b.get(key)))
    return ()


def _changed_symlink(before: Any, after: Any, paths: tuple[str, ...]) -> bool:
    left, right = _snapshot_value(before), _snapshot_value(after)
    a, b = left.get('files', {}), right.get('files', {})
    return any((isinstance(a.get(path), tuple) and a[path] and a[path][0] == 'symlink') or
               (isinstance(b.get(path), tuple) and b[path] and b[path][0] == 'symlink')
               for path in paths)


def _git_signature(snapshot):
    state = _snapshot_value(snapshot)
    git_state = state.get('git_state')
    if git_state is not None:
        return json.dumps(git_state, sort_keys=True, default=lambda item: item.hex()
                          if isinstance(item, bytes) else str(item))
    return tuple(state.get(key) for key in ('root', 'common_dir', 'branch', 'head'))


def _git_state_changed(before, after):
    """Detect owned Git changes while tolerating unrelated shared refs."""
    left, right = _snapshot_value(before), _snapshot_value(after)
    a, b = left.get('git_state'), right.get('git_state')
    if not isinstance(a, Mapping) or not isinstance(b, Mapping):
        return _git_signature(before) != _git_signature(after)
    for key in ('head', 'branch', 'branch_ref', 'git_dir', 'index', 'stash',
                'pointer', 'config_worktree', 'config_common'):
        if a.get(key) != b.get(key):
            return True
    before_refs, after_refs = a.get('refs', {}), b.get('refs', {})
    before_objects = a.get('objects', frozenset())
    before_head_commits = a.get('head_commits', frozenset())
    after_head_commits = b.get('head_commits', frozenset())
    d6_tip = {a.get('head'), a.get('branch_ref'), b.get('head'), b.get('branch_ref')}
    for name, object_id in after_refs.items():
        if before_refs.get(name) == object_id:
            continue
        if (object_id in d6_tip or object_id not in before_objects or
                (object_id in after_head_commits and object_id not in before_head_commits)):
            return True
    return False


def _git_status_paths(root):
    """Return dirty and ignored paths from porcelain v1."""
    raw = task_completion._git(
        root, ('status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignored'),
        limit=1024 * 1024)
    records = raw.split(b'\0')
    paths = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b' ':
            raise RouteError('ROUTE_GIT_STATUS_INVALID')
        paths.append(os.fsdecode(record[3:]))
        # Porcelain -z emits the original path in a second NUL record for a
        # rename/copy. Both ends are potentially outside the authorized set.
        if record[:2] in (b'R ', b'C ', b' R', b' C'):
            if index >= len(records) or not records[index]:
                raise RouteError('ROUTE_GIT_STATUS_INVALID')
            paths.append(os.fsdecode(records[index]))
            index += 1
    return tuple(paths)


def run_route(route: DeveloperRoute, request: Mapping[str, Any], *, gate,
              runner=None, boundary, snapshot):
    """Run one route only after dispatch, D6, and OS write-boundary checks."""
    started = time.monotonic()
    try:
        value = route.validate_request(request)
    except RouteError:
        raise
    except Exception:
        raise RouteError(_route_code(route, 'REQUEST_INVALID')) from None
    if not callable(gate):
        raise RouteError(_route_code(route, 'DISPATCH_DENIED'))
    try:
        decision = gate()
    except Exception:
        raise RouteError(_route_code(route, 'DISPATCH_DENIED')) from None
    if decision is not True and getattr(decision, 'allowed', None) is not True:
        raise RouteError(_route_code(route, 'DISPATCH_DENIED'), blocked_reason='OWNER_DISPATCH_DENIED')
    try:
        before_git = task_completion.collect_git_snapshot(value['root'])
    except Exception:
        raise RouteError(_route_code(route, 'D6_BINDING_INVALID')) from None
    if (before_git.branch != value['expected_branch'] or
            before_git.head != value['expected_head'] or not before_git.clean):
        raise RouteError(_route_code(route, 'D6_BINDING_INVALID'))
    try:
        before = snapshot(value['root'])
    except Exception:
        raise RouteError(_route_code(route, 'SNAPSHOT_FAILED')) from None
    try:
        before_status_paths = set(_git_status_paths(value['root']))
    except Exception:
        raise RouteError(_route_code(route, 'GIT_STATE_CHANGED')) from None
    if boundary is None or not all(callable(getattr(boundary, method, None))
                                   for method in ('prepare', 'verify')):
        raise RouteError('BLOCKED_WRITE_ISOLATION', blocked_reason='UNSUPPORTED_BOUNDARY')
    private_tmp = tempfile.mkdtemp(prefix='llmtool-route-')
    os.chmod(private_tmp, 0o700)
    requested_states = value.get('state_dirs', ())
    try:
        if type(requested_states) not in (tuple, list):
            raise ValueError
        private_root = Path(private_tmp).resolve(strict=True)
        for item in requested_states:
            if type(item) is not str or not os.path.isabs(item):
                raise ValueError
            state_path = Path(item).resolve(strict=False)
            if state_path == private_root or private_root not in state_path.parents:
                raise ValueError
    except (OSError, RuntimeError, ValueError):
        shutil.rmtree(private_tmp, ignore_errors=True)
        raise RouteError(_route_code(route, 'REQUEST_INVALID')) from None
    state_dirs = tuple(requested_states) + (private_tmp,)
    if type(value.get('log_path')) is str and not os.path.isabs(value['log_path']):
        value['log_path'] = str(Path(private_tmp) / value['log_path'])
    try:
        prefix, policy_sha256 = boundary.prepare(
            value['root'], value['write_paths'], state_dirs)
        if (type(prefix) not in (tuple, list) or
                any(type(item) is not str for item in prefix) or
                type(policy_sha256) is not str or
                re.fullmatch(r'[0-9a-f]{64}', policy_sha256) is None):
            raise ValueError('invalid boundary preparation')
        proof = boundary.verify(value['root'], value['write_paths'],
                                state_dirs, runner=runner or _default_runner)
    except Exception:
        shutil.rmtree(private_tmp, ignore_errors=True)
        raise RouteError('BLOCKED_WRITE_ISOLATION', blocked_reason='BOUNDARY_PREPARATION_FAILED') from None
    if (not isinstance(proof, Mapping) or proof.get('verified') is not True or
            proof.get('policy_sha256') != policy_sha256):
        shutil.rmtree(private_tmp, ignore_errors=True)
        raise RouteError('BLOCKED_WRITE_ISOLATION', blocked_reason='BOUNDARY_UNVERIFIED')
    try:
        env = route.build_env(private_tmp)
        argv = list(prefix) + route.build_argv(value['executable'], value)
    except Exception:
        shutil.rmtree(private_tmp, ignore_errors=True)
        raise RouteError(_route_code(route, 'REQUEST_INVALID')) from None
    execute = runner or _default_runner
    try:
        code, stdout, stderr = execute(argv, value['root'], env, value['timeout'])
    except TimeoutError:
        shutil.rmtree(private_tmp, ignore_errors=True)
        raise RouteError(_route_code(route, 'TIMEOUT')) from None
    except subprocess.TimeoutExpired:
        shutil.rmtree(private_tmp, ignore_errors=True)
        raise RouteError(_route_code(route, 'TIMEOUT')) from None
    except RouteError:
        shutil.rmtree(private_tmp, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(private_tmp, ignore_errors=True)
        raise RouteError(_route_code(route, 'RUNNER_FAILED'), facts={'exception_type': type(exc).__name__}) from None
    except BaseException:
        shutil.rmtree(private_tmp, ignore_errors=True)
        raise
    log_text = ''
    log_path = value.get('log_path')
    if log_path is not None:
        try:
            raw_log = Path(log_path).read_bytes()
            if len(raw_log) > 4 * 1024 * 1024:
                raise RouteError(_route_code(route, 'MODEL_UNVERIFIED'))
            log_text = raw_log.decode('utf-8')
        except RouteError:
            shutil.rmtree(private_tmp, ignore_errors=True)
            raise
        except (OSError, UnicodeError, TypeError):
            log_text = ''
    shutil.rmtree(private_tmp, ignore_errors=True)
    if (type(code) is not int or type(stdout) is not bytes or type(stderr) is not bytes or
            len(stdout) > MAX_STDOUT_BYTES or len(stderr) > MAX_STDERR_BYTES):
        raise RouteError(_route_code(route, 'RUNNER_RESULT_INVALID'))
    try:
        after = snapshot(value['root'])
        after_git = task_completion.collect_git_snapshot(value['root'])
    except Exception:
        raise RouteError(_route_code(route, 'GIT_STATE_CHANGED')) from None
    changed_paths = _changed_files(before, after)
    changed = changed_paths[:200]
    # Git identity/state changes must be classified before cleanliness so a
    # newly-created commit is not mislabeled as ordinary D6 dirtiness.
    if (_git_signature(before_git) != _git_signature(after_git) or
            _git_state_changed(before, after)):
        raise RouteError(_route_code(route, 'GIT_STATE_CHANGED'), facts={'changed_files': changed})
    allowed = set(value['write_paths'])
    try:
        after_status_paths = set(_git_status_paths(value['root']))
    except Exception:
        raise RouteError(_route_code(route, 'GIT_STATE_CHANGED'), facts={'changed_files': changed}) from None
    new_status_paths = after_status_paths - before_status_paths
    scope_violation = (any(path not in allowed for path in new_status_paths) or
                       any(path not in allowed for path in changed_paths) or
                       _changed_symlink(before, after, changed_paths))
    try:
        parsed = route.parse_output(stdout, stderr, log_text)
    except RouteError:
        raise
    except Exception:
        raise RouteError(_route_code(route, 'OUTPUT_INVALID')) from None
    if not isinstance(parsed, Mapping):
        raise RouteError(_route_code(route, 'OUTPUT_INVALID'))
    try:
        quota = route.quota_exhausted(parsed, stderr)
    except Exception:
        raise RouteError(_route_code(route, 'OUTPUT_INVALID')) from None
    quota_progress = {
        'state': 'QUOTA_EXHAUSTED', 'head_sha': after_git.head or 'UNKNOWN',
        'changed_files': list(changed), 'unfinished_checks': ['UNKNOWN'],
        'session_id': parsed.get('session_id')}
    if scope_violation:
        raise RouteError(_route_code(route, 'WRITE_SCOPE_VIOLATION'),
                         facts={'changed_files': changed},
                         progress=quota_progress if quota else None)
    if quota:
        raise RouteError(_route_code(route, 'QUOTA_EXHAUSTED'), progress=quota_progress)
    try:
        verified = route.verified_model(log_text, value['model'])
    except Exception:
        raise RouteError(_route_code(route, 'MODEL_UNVERIFIED')) from None
    if verified is None:
        raise RouteError(_route_code(route, 'MODEL_UNVERIFIED'))
    if verified != value['model']:
        raise RouteError(_route_code(route, 'MODEL_MISMATCH'))
    if code < 0:
        raise RouteError(_route_code(route, 'INTERRUPTED'))
    if code != 0:
        raise RouteError(_route_code(route, 'EXIT_NONZERO'))
    try:
        stdout_hash = hashlib.sha256(stdout).hexdigest()
        elapsed = int((time.monotonic() - started) * 1000)
        return RouteResult(route.name, str(parsed.get('status', 'DONE')),
                           str(parsed.get('summary', '')), parsed.get('session_id'),
                           verified, value['effort'], parsed.get('usage'), changed,
                           code, stdout_hash, elapsed,
                           getattr(execute, 'last_cleanup_status', 'UNKNOWN'), dict(proof))
    except Exception:
        raise RouteError(_route_code(route, 'OUTPUT_INVALID')) from None
