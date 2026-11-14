"""Bounded, verified delete/move of tracked files in a registered D6 worktree (#1502).

Four steps, each small and explicit:

* ``parse_ops(value)``: a JSON list of ``{op: delete, path}`` or
  ``{op: move, from, to}`` objects, at most 16, with strict repo-relative
  ASCII paths. A trailing ``/`` names a directory.
* ``plan(root, head, ops)``: read-only. Requires the expected HEAD and a clean
  tree, expands each op to concrete tracked files and checks the whole batch.
  Any failure refuses the whole batch; nothing changes.
* ``apply(root, plan)``: re-runs the plan checks, then one ``git rm`` or
  ``git mv`` per concrete file with argument lists. Destination parents are
  created first, one level at a time, only through directory file
  descriptors opened from the root with ``O_DIRECTORY|O_NOFOLLOW``
  (``os.mkdir(name, dir_fd=fd)``; never an absolute path). Then, right before
  each call, every existing component of source and destination is re-checked
  (lstat, no symlink, realpath inside the root); after each call the result
  paths are checked the same way. It never commits, pushes,
  resets, cleans or checks out. A mid-run failure stops at once and returns
  ``FAILED`` with ``isolate=True``: the caller must not run a Developer or
  publish, and recovery is to recreate the D6. Re-applying an already
  verified plan is a no-op; any other partial state is refused.
* ``verify(root, plan)``: the tree holds exactly the planned result (same blob
  ids as HEAD for moves, exact ``git status``) and returns the evidence.

Threat model. ``apply`` runs only on a single-writer, leased D6 worktree,
before any Developer process for the Task exists. The checks guard against
mistakes and stale or hostile tree contents (a symlink committed or left in
the tree, a path that escapes the root), not against a concurrent attacker:
any principal able to race these calls already has the same write access to
the worktree and needs no help from this module. The window inside each
``git rm`` / ``git mv`` call, between the final pre-check and git's own path
handling, is accepted (Owner decision: no transaction engine). ``_guard_after``
re-checks the result paths after every call; if a component was swapped
during that window it is detected and ``apply`` returns ``FAILED`` with
``isolate=True``, so the D6 is discarded rather than trusted.

This is not a generic shell or file API.
"""

import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess

from .errors import LLMToolError

MAX_OPS = 16
MAX_FILES = 500
MAX_PATH_LEN = 512
GIT_TIMEOUT_SECONDS = 60

INVALID = 'FILE_OPS_INVALID'
PATH_INVALID = 'FILE_OPS_PATH_INVALID'
HEAD_MISMATCH = 'FILE_OPS_HEAD_MISMATCH'
D6_NOT_CLEAN = 'FILE_OPS_D6_NOT_CLEAN'
PRECHECK_FAILED = 'FILE_OPS_PRECHECK_FAILED'
PARTIAL_STATE = 'FILE_OPS_PARTIAL_STATE'
VERIFY_FAILED = 'FILE_OPS_VERIFY_FAILED'
_GUARD = 'FILE_OPS_APPLY_GUARD'  # internal: becomes a FAILED/isolate result, never raised

# Parent creation needs directory file descriptors; without them apply fails
# closed. Captured at import so a patched ``os.mkdir`` cannot change the answer.
_DIR_FD_OK = (hasattr(os, 'O_DIRECTORY') and hasattr(os, 'O_NOFOLLOW')
              and os.mkdir in os.supports_dir_fd and os.open in os.supports_dir_fd)
_DIR_FLAGS = (os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
              | getattr(os, 'O_CLOEXEC', 0))

_HEAD_RE = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')
_REGULAR_MODES = frozenset({'100644', '100755'})
_MUTATIONS = frozenset({'rm', 'mv'})
_PLAN_KEYS = frozenset({'head', 'ops', 'files', 'sha256'})
_FILE_KEYS = {
    'delete': (frozenset({'op', 'path', 'mode', 'blob'}),),
    'move': (frozenset({'op', 'from', 'to', 'mode', 'blob'}),),
}


def _error(code, reason):
    exc = LLMToolError(f'{code}: {reason}')
    exc.code = code
    exc.reason = reason
    return exc


# ---------------------------------------------------------------- parse_ops

def _path_problem(value):
    """Return why ``value`` is not an allowed repo-relative path, or None."""
    if not isinstance(value, str):
        return 'not a string'
    if not value or len(value) > MAX_PATH_LEN:
        return f'empty or longer than {MAX_PATH_LEN} characters'
    if any(not (' ' <= ch <= '~') for ch in value):
        return 'not printable ASCII'
    if value.startswith('/'):
        return 'absolute path'
    if '\\' in value:
        return 'contains a backslash'
    body = value[:-1] if value.endswith('/') else value
    for part in body.split('/'):
        if part in ('', '.', '..'):
            return 'empty, "." or ".." component'
        if part.lower() == '.git':
            return '.git component'
    return None


def _op_path(value, index, field):
    problem = _path_problem(value)
    if problem:
        raise _error(PATH_INVALID, f'op {index} {field}: {problem}')
    return value


def parse_ops(value):
    """Parse and validate ops; return a list of normalized op dicts."""
    if isinstance(value, (bytes, bytearray)):
        try:
            value = bytes(value).decode('utf-8')
        except UnicodeDecodeError:
            raise _error(INVALID, 'ops are not UTF-8 JSON') from None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise _error(INVALID, 'ops are not valid JSON') from None
    if not isinstance(value, list):
        raise _error(INVALID, 'ops must be a JSON list')
    if not value:
        raise _error(INVALID, 'ops list is empty')
    if len(value) > MAX_OPS:
        raise _error(INVALID, f'more than {MAX_OPS} ops')
    ops = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise _error(INVALID, f'op {index}: not an object')
        keys = set(item)
        kind = item.get('op')
        if kind == 'delete' and keys == {'op', 'path'}:
            ops.append({'op': 'delete', 'path': _op_path(item['path'], index, 'path')})
        elif kind == 'move' and keys == {'op', 'from', 'to'}:
            src = _op_path(item['from'], index, 'from')
            dst = _op_path(item['to'], index, 'to')
            if src.endswith('/') != dst.endswith('/'):
                raise _error(INVALID, f'op {index}: from and to must both be files or both directories')
            if src == dst or (src.endswith('/') and (dst.startswith(src) or src.startswith(dst))):
                raise _error(INVALID, f'op {index}: from and to overlap')
            ops.append({'op': 'move', 'from': src, 'to': dst})
        else:
            raise _error(INVALID, f'op {index}: needs exactly {{op: delete, path}} or {{op: move, from, to}}')
    return ops


def ops_digest(ops):
    """sha256 of the canonical JSON of the parsed ops; binds them into a Task contract."""
    canonical = json.dumps(parse_ops(ops), sort_keys=True, separators=(',', ':'),
                           ensure_ascii=True)
    return hashlib.sha256(canonical.encode('ascii')).hexdigest()


def result_paths(files):
    """Every path a plan's ``git rm`` / ``git mv`` changes: deleted, moved-from, moved-to."""
    paths = set()
    for f in files:
        if f['op'] == 'delete':
            paths.add(f['path'])
        else:
            paths.update((f['from'], f['to']))
    return frozenset(paths)


# ---------------------------------------------------------------- git access

def _git_env():
    # Drop inherited GIT_* redirections (GIT_DIR, GIT_INDEX_FILE, ...); treat
    # every path argument literally, never as a glob or magic pathspec.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_LITERAL_PATHSPECS='1', GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
    return env


def _git_argv(args):
    return ['git', '-c', 'core.fsmonitor=false', *args]


def _git(root, args, code):
    """Run one read-only git command; return decoded stdout or raise ``code``."""
    try:
        proc = subprocess.run(
            _git_argv(args), cwd=root, env=_git_env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        raise _error(code, f'git {args[0]} could not run') from None
    if proc.returncode != 0:
        raise _error(code, f'git {args[0]} failed')
    return proc.stdout.decode('utf-8', 'surrogateescape')


def _mutate(root, args):
    """Run one ``git rm`` / ``git mv``; return True on success. No other verbs."""
    if not args or args[0] not in _MUTATIONS:
        raise _error(INVALID, 'only git rm and git mv are allowed')
    try:
        proc = subprocess.run(
            _git_argv(args), cwd=root, env=_git_env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _resolve_root(root):
    real = os.path.realpath(os.fspath(root))
    if not os.path.isdir(real):
        raise _error(PRECHECK_FAILED, 'root is not a directory')
    top = _git(real, ['rev-parse', '--show-toplevel'], PRECHECK_FAILED).rstrip('\n')
    if os.path.realpath(top) != real:
        raise _error(PRECHECK_FAILED, 'root is not the top level of a git worktree')
    return real


def _check_head_value(head):
    if not isinstance(head, str) or not _HEAD_RE.fullmatch(head):
        raise _error(INVALID, 'head must be a full lowercase commit id')
    return head


def _require_head(root, head):
    actual = _git(root, ['rev-parse', '--verify', '--quiet', 'HEAD^{commit}'], HEAD_MISMATCH).strip()
    if actual != head:
        raise _error(HEAD_MISMATCH, 'HEAD does not match the expected commit')


def _status(root):
    out = _git(root, ['status', '--porcelain=v1', '--untracked-files=all', '--no-renames', '-z'],
               PRECHECK_FAILED)
    return [(rec[:2], rec[3:]) for rec in out.split('\0') if rec]


def _ls_files(root):
    return {p for p in _git(root, ['ls-files', '-z'], PRECHECK_FAILED).split('\0') if p}


def _ls_tree(root, head, code):
    """Map path -> (mode, object id) for every entry of the HEAD tree."""
    entries = {}
    for rec in _git(root, ['ls-tree', '-r', '-z', '--full-tree', head], code).split('\0'):
        if rec:
            meta, _, path = rec.partition('\t')
            mode, _, obj = meta.split(' ')
            entries[path] = (mode, obj)
    return entries


def _index(root):
    """Map path -> list of (mode, object id, stage) from ``git ls-files -s``."""
    entries = {}
    for rec in _git(root, ['ls-files', '-s', '-z'], VERIFY_FAILED).split('\0'):
        if rec:
            meta, _, path = rec.partition('\t')
            mode, obj, stage = meta.split(' ')
            entries.setdefault(path, []).append((mode, obj, stage))
    return entries


# ---------------------------------------------------------------- plan

def _select(path, tracked, index):
    if path.endswith('/'):
        found = sorted(p for p in tracked if p.startswith(path))
        if not found:
            raise _error(PRECHECK_FAILED, f'op {index}: directory {path} has no tracked files')
        return found
    if path not in tracked:
        raise _error(PRECHECK_FAILED, f'op {index}: {path} is not a tracked file')
    return [path]


def _expand(ops, tracked):
    """Expand ops to concrete per-file ops against the tracked path set."""
    files = []
    for index, op in enumerate(ops, 1):
        if op['op'] == 'delete':
            files.extend({'op': 'delete', 'path': p} for p in _select(op['path'], tracked, index))
        else:
            src, dst = op['from'], op['to']
            for p in _select(src, tracked, index):
                to = dst + p[len(src):] if src.endswith('/') else dst
                files.append({'op': 'move', 'from': p, 'to': to})
        if len(files) > MAX_FILES:
            raise _error(PRECHECK_FAILED, f'more than {MAX_FILES} files')
    return files


def _with_blobs(files, head_blobs):
    out = []
    for f in files:
        source = f['path'] if f['op'] == 'delete' else f['from']
        entry = head_blobs.get(source)
        if entry is None or entry[0] not in _REGULAR_MODES:
            raise _error(PRECHECK_FAILED, f'{source} is not a regular file at HEAD')
        out.append(dict(f, mode=entry[0], blob=entry[1]))
    return out


def _fs_check(root, rel, must_exist, code):
    """lstat walk: no symlink or non-directory parent; realpath inside root."""
    parts = rel.split('/')
    current = root
    exists = True
    for i, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            exists = False
            break
        except OSError:
            raise _error(code, f'{rel}: cannot inspect path') from None
        if stat.S_ISLNK(st.st_mode):
            raise _error(code, f'{rel}: symlink component')
        if i < len(parts) - 1 and not stat.S_ISDIR(st.st_mode):
            raise _error(code, f'{rel}: a parent is not a directory')
        if i == len(parts) - 1 and must_exist and not stat.S_ISREG(st.st_mode):
            raise _error(code, f'{rel}: not a regular file')
    real = os.path.realpath(os.path.join(root, rel))
    if real == root or os.path.commonpath([root, real]) != root:
        raise _error(code, f'{rel}: resolves outside the worktree')
    if must_exist and not exists:
        raise _error(code, f'{rel}: missing from the worktree')
    if not must_exist and exists:
        raise _error(code, f'{rel}: destination already exists')


def _precheck(root, files, tracked):
    tracked_lower = {p.lower() for p in tracked}
    tracked_dirs = set()
    for p in tracked_lower:
        parts = p.split('/')
        for i in range(1, len(parts)):
            tracked_dirs.add('/'.join(parts[:i]))
    seen = set()
    for f in files:
        sources = [f['path']] if f['op'] == 'delete' else [f['from']]
        dests = [] if f['op'] == 'delete' else [f['to']]
        for p in sources + dests:
            problem = _path_problem(p)
            if problem:
                raise _error(PRECHECK_FAILED, f'planned path is not allowed: {problem}')
            key = p.lower()  # also catches collisions on case-insensitive filesystems
            if key in seen:
                raise _error(PRECHECK_FAILED, f'{p} is touched twice')
            seen.add(key)
        for s in sources:
            if s not in tracked:
                raise _error(PRECHECK_FAILED, f'{s} is not tracked')
            _fs_check(root, s, True, PRECHECK_FAILED)
        for d in dests:
            if d.lower() in tracked_lower or d.lower() in tracked_dirs:
                raise _error(PRECHECK_FAILED, f'{d}: destination already exists in the index')
            _fs_check(root, d, False, PRECHECK_FAILED)
    for key in seen:
        parts = key.split('/')
        for i in range(1, len(parts)):
            if '/'.join(parts[:i]) in seen:
                raise _error(PRECHECK_FAILED, f'{key} is inside another planned path')


def _digest(head, ops, files):
    try:
        canonical = json.dumps({'head': head, 'ops': ops, 'files': files},
                               sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    except (TypeError, ValueError):
        raise _error(INVALID, 'plan is not canonical JSON') from None
    return hashlib.sha256(canonical.encode('ascii')).hexdigest()


def _seal(head, ops, files):
    return {'head': head, 'ops': ops, 'files': files, 'sha256': _digest(head, ops, files)}


def _make_plan(root, head, ops):
    root = _resolve_root(root)
    head = _check_head_value(head)
    ops = parse_ops(ops)
    _require_head(root, head)
    if _status(root):
        raise _error(D6_NOT_CLEAN, 'worktree has uncommitted or untracked changes')
    tracked = _ls_files(root)
    files = _expand(ops, tracked)
    _precheck(root, files, tracked)
    files = _with_blobs(files, _ls_tree(root, head, PRECHECK_FAILED))
    return _seal(head, ops, files)


def plan(root, head, ops):
    """Read-only whole-batch precheck; return the sealed plan or raise."""
    return _make_plan(root, head, ops)


def _open_plan(value):
    if not isinstance(value, dict) or set(value) != _PLAN_KEYS:
        raise _error(INVALID, 'plan is malformed')
    head = _check_head_value(value['head'])
    ops = parse_ops(value['ops'])
    files = value['files']
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise _error(INVALID, 'plan files are malformed')
    for f in files:
        if not (isinstance(f, dict) and all(isinstance(v, str) for v in f.values())
                and set(f) in _FILE_KEYS.get(f.get('op'), ())):
            raise _error(INVALID, 'plan files are malformed')
    if value['sha256'] != _digest(head, ops, files):
        raise _error(INVALID, 'plan digest does not match its content')
    return head, ops, files


_BLOB_RE = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')


def check_plan(value):
    """Strictly check a sealed plan without touching any tree; return ``value``.

    Besides the shape and digest that ``apply`` checks, every planned path must
    be an allowed repo-relative file path, every mode a regular-file mode and
    every blob a full object id. Used to validate persisted evidence.
    """
    _, _, files = _open_plan(value)
    for f in files:
        paths = [f['path']] if f['op'] == 'delete' else [f['from'], f['to']]
        if (any(_path_problem(p) or p.endswith('/') for p in paths) or
                f['mode'] not in _REGULAR_MODES or not _BLOB_RE.fullmatch(f['blob'])):
            raise _error(INVALID, 'plan files are malformed')
    return value


# ---------------------------------------------------------------- verify

def verify(root, plan):
    """Check the tree holds exactly the planned result; return the evidence."""
    root = _resolve_root(root)
    head, ops, files = _open_plan(plan)
    _require_head(root, head)
    try:
        head_blobs = _ls_tree(root, head, VERIFY_FAILED)
        expected = _with_blobs(_expand(ops, head_blobs), head_blobs)
    except LLMToolError as exc:
        raise _error(VERIFY_FAILED, f'plan cannot be rebuilt from HEAD ({exc.code})') from None
    if expected != files:
        raise _error(VERIFY_FAILED, 'plan files do not match HEAD')
    index = _index(root)
    expected_status = set()
    for f in files:
        gone = f['path'] if f['op'] == 'delete' else f['from']
        if gone in index:
            raise _error(VERIFY_FAILED, f'{gone} is still in the index')
        if os.path.lexists(os.path.join(root, gone)):
            raise _error(VERIFY_FAILED, f'{gone} still exists in the worktree')
        expected_status.add(('D ', gone))
        if f['op'] == 'move':
            if index.get(f['to']) != [(f['mode'], f['blob'], '0')]:
                raise _error(VERIFY_FAILED, f"{f['to']} does not hold the planned blob")
            _fs_check(root, f['to'], True, VERIFY_FAILED)
            expected_status.add(('A ', f['to']))
    actual = _status(root)
    if len(actual) != len(expected_status) or set(actual) != expected_status:
        raise _error(VERIFY_FAILED, 'git status does not list exactly the planned paths')
    return {'status': 'VERIFIED', 'head': head, 'ops': ops, 'files': files,
            'plan_sha256': plan['sha256']}


# ---------------------------------------------------------------- apply

def _failed(sealed, completed, reason):
    return {'status': 'FAILED', 'isolate': True, 'noop': False, 'completed': completed,
            'total': len(sealed['files']), 'reason': reason,
            'head': sealed['head'], 'plan_sha256': sealed['sha256']}


def _open_dir(name, dir_fd, rel):
    """Open ``name`` under ``dir_fd`` as a directory without following a symlink."""
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    except OSError:
        raise _error(_GUARD, f'{rel}: parent {name} is a symlink or not a directory') from None


def _child_dir(name, dir_fd, rel):
    """Return an fd for directory ``name`` under ``dir_fd``, creating it if absent."""
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        pass
    except OSError:
        raise _error(_GUARD, f'{rel}: parent {name} is a symlink or not a directory') from None
    try:
        os.mkdir(name, dir_fd=dir_fd)
    except FileExistsError:
        pass  # something appeared; the O_NOFOLLOW open below decides
    except OSError:
        raise _error(_GUARD, f'{rel}: cannot create parent {name}') from None
    return _open_dir(name, dir_fd, rel)


def _make_parents(root, rel):
    """Create the parents of ``rel`` one level at a time; never follow a symlink.

    The walk holds a directory fd opened from ``root`` with
    ``O_DIRECTORY|O_NOFOLLOW`` and creates each missing level with
    ``os.mkdir(name, dir_fd=fd)``, so a component swapped for a symlink is
    refused, and a swapped ancestor cannot redirect creation outside ``root``.
    """
    parent = posixpath.dirname(rel)
    if not parent:
        return
    if not _DIR_FD_OK:
        raise _error(_GUARD, 'directory file descriptors are not supported on this platform')
    fd = _open_dir(root, None, rel)
    try:
        for part in parent.split('/'):
            child = _child_dir(part, fd, rel)
            os.close(fd)
            fd = child
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise _error(_GUARD, f'{rel}: parent {part} is not a directory')
    finally:
        os.close(fd)


def _guard_before(root, f):
    """Create destination parents, then re-validate source and destination.

    The checks run after parent creation and immediately before the git
    call, so a swap during parent creation is still caught.
    """
    if f['op'] == 'delete':
        _fs_check(root, f['path'], True, _GUARD)
        return
    _make_parents(root, f['to'])
    _fs_check(root, f['from'], True, _GUARD)
    _fs_check(root, f['to'], False, _GUARD)


def _guard_after(root, f):
    """The result path is inside the root and no component is a symlink."""
    if f['op'] == 'delete':
        _fs_check(root, f['path'], False, _GUARD)
        return
    _fs_check(root, f['from'], False, _GUARD)
    _fs_check(root, f['to'], True, _GUARD)


def _execute(root, sealed):
    for index, f in enumerate(sealed['files']):
        verb = 'rm' if f['op'] == 'delete' else 'mv'
        done = index
        try:
            _guard_before(root, f)
            if verb == 'rm':
                ok = _mutate(root, ['rm', '-q', '--', f['path']])
            else:
                ok = _mutate(root, ['mv', '--', f['from'], f['to']])
            if ok:
                done = index + 1
                _guard_after(root, f)
        except LLMToolError as exc:
            return _failed(sealed, done, f'path check failed on file {index + 1}: {exc.reason}')
        except OSError:
            ok = False
        if not ok:
            return _failed(sealed, done, f'git {verb} failed on file {index + 1}')
    try:
        evidence = verify(root, sealed)
    except LLMToolError as exc:
        return _failed(sealed, len(sealed['files']), f'post-apply verification failed ({exc.code})')
    return {'status': 'APPLIED', 'isolate': False, 'noop': False, 'evidence': evidence}


def apply(root, plan):
    """Apply a sealed plan with git rm / git mv; never commit, push, reset, clean or checkout.

    Raises before any change when the plan or tree is refused. Returns
    ``APPLIED`` or, for an already verified tree, ``VERIFIED`` with
    ``noop=True``. A mid-run failure returns ``FAILED`` with ``isolate=True``.

    Threat model: call this only on a single-writer, leased D6 before any
    Developer process exists. A principal able to race it already has equal
    write access to the worktree. The window inside each git call, after the
    final pre-check, is accepted (Owner: no transaction engine);
    ``_guard_after`` detects a component swapped in that window and the
    result is ``FAILED`` with ``isolate=True`` (recreate the D6).
    """
    root = _resolve_root(root)
    head, ops, files = _open_plan(plan)
    _require_head(root, head)
    entries = _status(root)
    if not entries:
        fresh = _make_plan(root, head, ops)
        if fresh['sha256'] != plan['sha256']:
            raise _error(PRECHECK_FAILED, 'plan does not match the current tree')
        return _execute(root, fresh)
    planned = set()
    for f in files:
        planned.update([f['path']] if f['op'] == 'delete' else [f['from'], f['to']])
    if any(path not in planned for _, path in entries):
        raise _error(D6_NOT_CLEAN, 'worktree has changes outside the plan')
    try:
        evidence = verify(root, plan)
    except LLMToolError:
        raise _error(PARTIAL_STATE, 'worktree holds part of the planned change; recreate the D6') from None
    return {'status': 'VERIFIED', 'isolate': False, 'noop': True, 'evidence': evidence}
