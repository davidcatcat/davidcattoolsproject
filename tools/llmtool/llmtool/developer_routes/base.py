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
# Route timeouts come from developer-routes.json (default_timeout and
# max_timeout per route). A route without registry limits keeps the former
# fixed 600-second default and maximum. No route may exceed the hard cap.
DEFAULT_TIMEOUT_SECONDS = 600
HARD_TIMEOUT_CAP_SECONDS = 1800
MAX_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_SECONDS
TIMEOUT_KEYS = frozenset({'default_timeout', 'max_timeout'})


class RouteError(LLMToolError):
    """Fixed-text route failure with bounded, non-content observations."""

    def __init__(self, code: str, *, blocked_reason: str | None = None,
                 progress: Mapping[str, Any] | None = None,
                 facts: Mapping[str, Any] | None = None,
                 provider_evidence: Mapping[str, Any] | None = None):
        if type(code) is not str or re.fullmatch(r'[A-Z0-9_]{1,64}', code) is None:
            code = 'ROUTE_ERROR'
        super().__init__('Developer route execution unavailable; details omitted')
        self.code = code
        self.blocked_reason = blocked_reason
        self.progress = dict(progress) if progress is not None else None
        self.observed_facts = dict(facts or {})
        self.facts = self.observed_facts
        # Only run_route sets this, and only from the route's parsed output.
        self.provider_evidence = (dict(provider_evidence)
                                  if provider_evidence is not None else None)


# Design c6876861575 items 1-2: the evidence a route may return from its own
# parsed provider output. Exit 0, cleanup or a stdout digest alone give none.
PROVIDER_EVIDENCE_BASES = frozenset(('CLI_USAGE', 'STEP_USAGE', 'AGY_STATUS', 'GATEWAY_USAGE'))
QUOTA_EXHAUSTED_EVIDENCE = {'condition': 'QUOTA_EXHAUSTED'}


def accepted_evidence(basis):
    """Return {basis, accepted: True} for a known basis, else None."""
    if type(basis) is str and basis in PROVIDER_EVIDENCE_BASES:
        return {'basis': basis, 'accepted': True}
    return None


def usage_reported(usage):
    """Whether normalized usage reports a positive token total."""
    return (isinstance(usage, Mapping) and type(usage.get('total_tokens')) is int and
            usage['total_tokens'] > 0)


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
    provider_evidence: Mapping[str, Any] | None = None


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

    def prepare_files(self, tmpdir: str, request: Mapping[str, Any]) -> None:
        """Optional hook, default no-op: create route-owned input files before argv is built."""
        return None

    def collect(self, tmpdir: str, request: Mapping[str, Any]) -> None:
        """Optional hook, default no-op: collect route outputs while the private tmpdir exists."""
        return None

    def configure_boundary(self, boundary: Any) -> Any:
        """Return the write boundary this route runs under; verified as usual."""
        return boundary

    def validate_exit(self, exit_code: int, parsed: Mapping[str, Any]) -> None:
        """Optional hook, default accepts: check provider status and process exit consistency."""
        return None

    def validate_status(self, parsed: Mapping[str, Any]) -> None:
        """Optional hook, default accepts: map a consistent provider status to route errors."""
        return None

    def provider_basis(self, parsed: Mapping[str, Any]) -> str | None:
        """Optional hook, default none: the evidence basis proven by parsed output."""
        return None

    def provider_evidence(self, parsed: Mapping[str, Any]) -> dict[str, Any] | None:
        """Return {basis, accepted: True} from this route's parsed output, else None."""
        try:
            return accepted_evidence(self.provider_basis(parsed))
        except Exception:
            return None

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
        requested = value.get('timeout')
        if 'timeout' in value and requested is None:
            raise RouteError(f'{self.name.upper()}_REQUEST_INVALID')
        try:
            timeout = resolve_route_timeout(self, requested)
        except RouteError as exc:
            raise RouteError(f'{self.name.upper()}_REQUEST_INVALID',
                             facts={'timeout_refusal': exc.code}) from None
        value.update(root=str(root), model=model, effort=effort, timeout=timeout,
                     write_paths=tuple(paths))
        return value


def timeout_limits_ok(default: Any, maximum: Any) -> bool:
    """Return True for int limits with 1 <= default <= maximum <= hard cap."""
    return (type(default) is int and type(maximum) is int and
            1 <= default <= maximum <= HARD_TIMEOUT_CAP_SECONDS)


def _route_timeout_limits(route: Any, config: Any) -> tuple[int, int]:
    """Return (default, maximum) seconds for route, or raise ROUTE_CONFIG_INVALID.

    A route name (str) must be registered. A DeveloperRoute is looked up by
    its name (case-folded, '_' read as '-'); an unregistered adapter keeps the
    600-second baseline. The registry is authoritative; adapter config cannot
    raise a registered route's limits.
    """
    if isinstance(route, DeveloperRoute):
        name = getattr(route, 'name', None)
        if type(name) is not str:
            raise RouteError('ROUTE_CONFIG_INVALID')
        key, registered_only = name.lower().replace('_', '-'), False
    elif type(route) is str:
        key, registered_only = route, True
    else:
        raise RouteError('ROUTE_CONFIG_INVALID')
    if config is None:
        from . import load_route_config
        config = load_route_config()
    routes = config.get('routes') if isinstance(config, Mapping) else None
    if not isinstance(routes, Mapping):
        raise RouteError('ROUTE_CONFIG_INVALID')
    entry = routes.get(key)
    if entry is None:
        if registered_only:
            raise RouteError('ROUTE_CONFIG_INVALID')
        return DEFAULT_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS
    if not isinstance(entry, Mapping):
        raise RouteError('ROUTE_CONFIG_INVALID')
    present = TIMEOUT_KEYS & set(entry)
    if not present:
        return DEFAULT_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS
    if (present != TIMEOUT_KEYS or
            not timeout_limits_ok(entry['default_timeout'], entry['max_timeout'])):
        raise RouteError('ROUTE_CONFIG_INVALID')
    return entry['default_timeout'], entry['max_timeout']


def resolve_route_timeout(route: Any, requested: Any = None, *,
                          config: Mapping[str, Any] | None = None) -> int:
    """Return the effective timeout in seconds for one route run.

    route is a registered route name or a DeveloperRoute; config is a
    load_route_config() result (None loads the repository file). requested
    None gives the route's default_timeout. Refusals (RouteError code):

    * TIMEOUT_INVALID: requested is not an int (bool and float included) or
      is below 1;
    * TIMEOUT_OVER_HARD_CAP: requested is above 1800, for every route;
    * TIMEOUT_OVER_ROUTE_MAX: requested is above the route's max_timeout;
    * ROUTE_CONFIG_INVALID: unknown route name or invalid timeout limits.
    """
    default, maximum = _route_timeout_limits(route, config)
    if requested is None:
        return default
    if type(requested) is not int or requested < 1:
        raise RouteError('TIMEOUT_INVALID')
    if requested > HARD_TIMEOUT_CAP_SECONDS:
        raise RouteError('TIMEOUT_OVER_HARD_CAP')
    if requested > maximum:
        raise RouteError('TIMEOUT_OVER_ROUTE_MAX')
    return requested


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

    def git(*args, limit):
        try:
            return task_completion._git(root, args, limit=limit)
        except LLMToolError:
            raise RouteError('ROUTE_GIT_INSPECTION_FAILED') from None

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
    branch = git('branch', '--show-current', limit=4096).decode().strip()
    branch_ref = (git(
        'rev-parse', '--verify', f'refs/heads/{branch}', limit=4096).decode().strip()
                  if branch else '')
    git_dir = Path(git(
        'rev-parse', '--path-format=absolute', '--git-dir', limit=4096).decode().strip())
    common_dir = Path(git(
        'rev-parse', '--path-format=absolute', '--git-common-dir', limit=4096).decode().strip())
    refs_raw = git(
        'for-each-ref',
        '--format=%(refname) %(objectname) %(*objectname) %(objecttype)',
        limit=1024 * 1024)
    refs = {}
    for line in refs_raw.decode().splitlines():
        name, object_id, peeled_id, object_type = line.split(' ', 3)
        commit_id = peeled_id or (object_id if object_type == 'commit' else '')
        refs[name] = (object_id, commit_id)
    # Read after refs: a list older than refs could miss a new worktree's branch.
    branch_line = b'branch refs/heads/'
    worktree_branches = frozenset(
        os.fsdecode(line[len(branch_line):])
        for line in git('worktree', 'list', '--porcelain', limit=1024 * 1024).split(b'\n')
        if line.startswith(branch_line)) - {branch}
    head = git('rev-parse', '--verify', 'HEAD', limit=4096).decode().strip()
    head_commits = frozenset(git(
        'rev-list', '--topo-order', 'HEAD', limit=1024 * 1024).decode().splitlines())

    def file_digest(path):
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return None

    def common_config_digest(path, current_branch, existing_branches, default_branches):
        """Fingerprint common config except another existing branch's tracking data."""
        try:
            raw = task_completion._git(
                root, ('config', '--no-includes', '--null', '--list', '--file', str(path)),
                limit=1024 * 1024)
        except Exception:
            # Preserve a marker so _git_state_changed can reject an invalid
            # config even when both snapshots see the same malformed bytes.
            try:
                raw = path.read_bytes()
            except OSError:
                raw = b''
            return 'INVALID:' + hashlib.sha256(raw).hexdigest()
        entries = []
        for record in raw.split(b'\0'):
            if not record:
                continue
            key, separator, value = record.partition(b'\n')
            if not separator:
                # In --null --list output a key without a value separator is
                # Git's valueless boolean key, equivalent to an explicit true.
                value = b'true'
            # Git writes remote/merge when another Task pushes an existing
            # non-default branch. Keep all other branch variables significant.
            if key.startswith(b'branch.'):
                subsection, dot, variable = key[len(b'branch.'):].rpartition(b'.')
                if (dot and subsection != os.fsencode(current_branch) and
                        subsection in existing_branches and
                        subsection not in default_branches and
                        variable in (b'remote', b'merge')):
                    continue
            entries.append((key, value))
        digest = hashlib.sha256()
        for key, value in entries:
            digest.update(len(key).to_bytes(8, 'big'))
            digest.update(key)
            digest.update(len(value).to_bytes(8, 'big'))
            digest.update(value)
        return digest.hexdigest()

    # Only tracking values for branches that already exist can be concurrent
    # push -u bookkeeping. Protect conventional and configured default branches.
    existing_branches = {
        os.fsencode(name[len('refs/heads/'):]) for name in refs
        if name.startswith('refs/heads/')
    }
    default_branches = {b'main', b'master', b'default'}
    try:
        configured_default = task_completion._git(
            root, ('config', '--no-includes', '--get', 'init.defaultBranch'),
            limit=4096).decode().strip()
        if configured_default:
            default_branches.add(os.fsencode(configured_default))
    except LLMToolError:
        pass
    try:
        remote_head = task_completion._git(
            root, ('symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD'),
            limit=4096).decode().strip()
        if remote_head.startswith('refs/remotes/origin/'):
            default_branches.add(os.fsencode(remote_head[len('refs/remotes/origin/'):]))
    except LLMToolError:
        pass

    git = {
        'head': head,
        'branch': branch,
        'branch_ref': branch_ref,
        'git_dir': str(git_dir),
        'index': git('ls-files', '--stage', '-z', limit=1024 * 1024),
        'stash': git('for-each-ref', '--format=%(objectname)',
                     'refs/stash', limit=4096).strip(),
        'refs': refs,
        'worktree_branches': worktree_branches,
        'head_commits': head_commits,
        'config_worktree': file_digest(git_dir / 'config.worktree'),
        'config_common': common_config_digest(
            common_dir / 'config', branch, existing_branches, default_branches),
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


def _other_worktree_ref(name, worktree_branches):
    """Return whether ``name`` is a branch, or its tracking ref, of another worktree."""
    if name.startswith('refs/heads/'):
        return name[len('refs/heads/'):] in worktree_branches
    if name.startswith('refs/remotes/'):
        _remote, _separator, branch = name[len('refs/remotes/'):].partition('/')
        return branch in worktree_branches
    return False


def _git_state_difference(before, after):
    """Return the first differing fixed state label, or None.

    Labels are a closed set and carry no ref name, SHA, path or config value.
    Unrelated shared refs and other worktrees' branches are tolerated.
    """
    left, right = _snapshot_value(before), _snapshot_value(after)
    a, b = left.get('git_state'), right.get('git_state')
    if not isinstance(a, Mapping) or not isinstance(b, Mapping):
        return 'IDENTITY' if _git_signature(before) != _git_signature(after) else None
    for key in ('head', 'branch', 'branch_ref', 'git_dir', 'index', 'stash',
                'pointer', 'config_worktree', 'config_common'):
        if (key == 'config_common' and
                (str(a.get(key, '')).startswith('INVALID:') or
                 str(b.get(key, '')).startswith('INVALID:'))):
            return key.upper()
        if a.get(key) != b.get(key):
            return key.upper()
    before_refs, after_refs = a.get('refs', {}), b.get('refs', {})
    before_head_commits = a.get('head_commits', frozenset())
    d6_new_commits = b.get('head_commits', frozenset()) - before_head_commits
    worktree_branches = b.get('worktree_branches', frozenset())
    for name in set(before_refs) | set(after_refs):
        before_ref, after_ref = before_refs.get(name), after_refs.get(name)
        if before_ref == after_ref:
            continue
        if name == 'refs/stash' or name.startswith('refs/notes/'):
            return 'REFS'
        if _other_worktree_ref(name, worktree_branches):
            continue
        if after_ref and (after_ref[1] == b.get('head') or
                          after_ref[1] in d6_new_commits):
            return 'REFS'
    return None


def _git_state_changed(before, after):
    """Detect owned Git changes while tolerating unrelated shared refs."""
    return _git_state_difference(before, after) is not None


def _git_state_refusal(route, label):
    return RouteError(_route_code(route, f'GIT_STATE_CHANGED_{label}'),
                      facts={'git_state_label': label})


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


def _result_evidence(route, parsed):
    """Basis evidence from the route's own parsed output; any doubt gives none."""
    hook = getattr(route, 'provider_evidence', None)
    if not callable(hook):
        return None
    try:
        evidence = hook(parsed)
    except Exception:
        return None
    if (isinstance(evidence, Mapping) and set(evidence) == {'basis', 'accepted'} and
            evidence['accepted'] is True):
        return accepted_evidence(evidence['basis'])
    return None


def _exit_diagnostic(code, stderr):
    """#1606: exit code plus a bounded, screened, one-line stderr tail."""
    from ..task_submission_cli import _redact_rejected_output
    from ..test_output_excerpts import _emitted_sensitive
    text = _redact_rejected_output(stderr.decode('utf-8', 'replace'))
    text = ''.join(c for c in text.replace('\n', ' | ') if ' ' <= c <= '~')
    reason = 'exit_code=%d stderr_tail=%s' % (code, text[-1536:])
    if _emitted_sensitive(reason):
        return 'exit_code=%d stderr_tail=[withheld]' % code
    return reason


def run_route(route: DeveloperRoute, request: Mapping[str, Any], *, gate,
              runner=None, boundary, snapshot, on_launch=None):
    """Run one route only after dispatch, D6, and OS write-boundary checks.

    ``on_launch`` (optional, no arguments) is called once at the launch
    boundary: after request validation, the dispatch/D6 gate, write-boundary
    verification and route input preparation, right before the process
    starts. No refusal before that point calls it.
    """
    started = time.monotonic()
    if on_launch is not None and not callable(on_launch):
        raise RouteError(_route_code(route, 'REQUEST_INVALID'))
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
        raise _git_state_refusal(route, 'SNAPSHOT') from None
    try:
        before_status_paths = set(_git_status_paths(value['root']))
    except Exception:
        raise _git_state_refusal(route, 'SNAPSHOT') from None
    try:
        boundary = route.configure_boundary(boundary)
    except Exception:
        raise RouteError('BLOCKED_WRITE_ISOLATION',
                         blocked_reason='UNSUPPORTED_BOUNDARY') from None
    if boundary is None or not all(callable(getattr(boundary, method, None))
                                   for method in ('prepare', 'verify')):
        raise RouteError('BLOCKED_WRITE_ISOLATION', blocked_reason='UNSUPPORTED_BOUNDARY')
    root = Path(value['root'])
    root_git = root / '.git'
    resolved_git = root_git.resolve(strict=False)
    folders_to_create = []
    for item in value['write_paths']:
        # Refuse write paths with any .git component regardless of case.
        if any(part.casefold() == '.git' for part in Path(item).parts):
            raise RouteError('BLOCKED_WRITE_ISOLATION',
                             blocked_reason='BOUNDARY_PREPARATION_FAILED')
        rel_parent = Path(item).parent
        target_parent = root / rel_parent
        try:
            resolved_parent = target_parent.resolve(strict=False)
        except Exception:
            raise RouteError('BLOCKED_WRITE_ISOLATION',
                             blocked_reason='BOUNDARY_PREPARATION_FAILED') from None
        if resolved_parent != root and root not in resolved_parent.parents:
            raise RouteError('BLOCKED_WRITE_ISOLATION',
                             blocked_reason='BOUNDARY_PREPARATION_FAILED')
        if (target_parent == root_git or root_git in target_parent.parents or
                resolved_parent == resolved_git or resolved_git in resolved_parent.parents or
                '.git' in rel_parent.parts):
            raise RouteError('BLOCKED_WRITE_ISOLATION',
                             blocked_reason='BOUNDARY_PREPARATION_FAILED')
        current = root
        for part in Path(item).parts:
            current = current / part
            if current.is_symlink():
                raise RouteError('BLOCKED_WRITE_ISOLATION',
                                 blocked_reason='BOUNDARY_PREPARATION_FAILED')
        if not target_parent.exists():
            if target_parent not in folders_to_create:
                folders_to_create.append(target_parent)
        elif not target_parent.is_dir():
            raise RouteError('BLOCKED_WRITE_ISOLATION',
                             blocked_reason='BOUNDARY_PREPARATION_FAILED')
    for folder in folders_to_create:
        try:
            os.makedirs(folder, mode=0o755, exist_ok=True)
        except OSError:
            raise RouteError('BLOCKED_WRITE_ISOLATION',
                             blocked_reason='BOUNDARY_PREPARATION_FAILED') from None
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
        value['_route_tmpdir'] = private_tmp
        route.prepare_files(private_tmp, value)
        env = route.build_env(private_tmp)
        argv = list(prefix) + route.build_argv(value['executable'], value)
        if on_launch is not None:
            on_launch()
    except RouteError:
        shutil.rmtree(private_tmp, ignore_errors=True)
        raise
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
    try:
        route.collect(private_tmp, value)
    except RouteError:
        shutil.rmtree(private_tmp, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(private_tmp, ignore_errors=True)
        raise RouteError(_route_code(route, 'COLLECT_FAILED')) from None
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
        raise _git_state_refusal(route, 'SNAPSHOT') from None
    changed_paths = _changed_files(before, after)
    changed = changed_paths[:200]
    # Git identity/state changes must be classified before cleanliness so a
    # newly-created commit is not mislabeled as ordinary D6 dirtiness. The
    # specific label goes first: a new commit also changes the identity.
    label = _git_state_difference(before, after)
    if label is None and _git_signature(before_git) != _git_signature(after_git):
        label = 'IDENTITY'
    if label is not None:
        raise _git_state_refusal(route, label)
    allowed = set(value['write_paths'])
    try:
        after_status_paths = set(_git_status_paths(value['root']))
    except Exception:
        raise _git_state_refusal(route, 'SNAPSHOT') from None
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
        route.validate_exit(code, parsed)
    except RouteError:
        raise
    except Exception:
        raise RouteError(_route_code(route, 'OUTPUT_INVALID')) from None
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
        facts = {}
        gateway_code = getattr(route, '_gateway_code', None)
        if type(gateway_code) is str and re.fullmatch(r'[A-Z0-9_]{1,48}', gateway_code):
            facts['gateway_code'] = gateway_code
        raise RouteError(_route_code(route, 'QUOTA_EXHAUSTED'),
                         progress=quota_progress, facts=facts,
                         provider_evidence=QUOTA_EXHAUSTED_EVIDENCE)
    try:
        route.validate_status(parsed)
    except RouteError:
        raise
    except Exception:
        raise RouteError(_route_code(route, 'OUTPUT_INVALID')) from None
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
        raise RouteError(_route_code(route, 'EXIT_NONZERO'), blocked_reason=(
            _exit_diagnostic(code, stderr) if route.name == 'agy' else None))
    try:
        stdout_hash = hashlib.sha256(stdout).hexdigest()
        elapsed = int((time.monotonic() - started) * 1000)
        return RouteResult(route.name, str(parsed.get('status', 'DONE')),
                           str(parsed.get('summary', '')), parsed.get('session_id'),
                           verified, value['effort'], parsed.get('usage'), changed,
                           code, stdout_hash, elapsed,
                           getattr(execute, 'last_cleanup_status', 'UNKNOWN'), dict(proof),
                           _result_evidence(route, parsed))
    except Exception:
        raise RouteError(_route_code(route, 'OUTPUT_INVALID')) from None
