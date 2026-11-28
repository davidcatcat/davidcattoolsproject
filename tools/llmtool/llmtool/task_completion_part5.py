"""Git-side stacked-Task scope checks for task_completion (#2084).

The caller resolves the chain from Task records. Each entry is a dict with
``source_sha``, ``result_sha`` and ``write_paths``. This module only validates
the Git shape and attributes each Task's own commits to that Task's write paths;
a trusted-main merge in the bound Task's range may carry paths in the union of
the whole chain. Any doubt fails closed as RESULT_SCOPE_MISMATCH.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import sys
import time

from .errors import LLMToolError

_m = sys.modules[__package__ + '.task_completion']

_ENTRY_KEYS = frozenset(('source_sha', 'result_sha', 'write_paths'))


def _stop(*args, **kwargs):
    from . import task_completion as _m
    return _m._stop(*args, **kwargs)


def _git(*args, **kwargs):
    from . import task_completion as _m
    return _m._git(*args, **kwargs)


def _relative_path(path):
    # Match the Task write-path validators: at most 512 characters of
    # printable ASCII, so control and non-ASCII paths fail closed here too.
    return (type(path) is str and bool(path) and len(path) <= 512 and
            path.isascii() and path.isprintable() and
            not path.startswith('/') and '\\' not in path and
            all(part not in ('', '.', '..') for part in path.split('/')))


def _normalize_stacked_chain(root, stacked_chain, bound_source_sha):
    """Validate a caller-supplied chain and return its canonical copy."""
    if (type(stacked_chain) is not list or not stacked_chain or
            type(bound_source_sha) is not str or
            _m._HEX40.fullmatch(bound_source_sha) is None):
        _stop('RESULT_SCOPE_MISMATCH')
    normalized = []
    for entry in stacked_chain:
        if type(entry) is not dict or set(entry) != _ENTRY_KEYS:
            _stop('RESULT_SCOPE_MISMATCH')
        source_sha = entry['source_sha']
        result_sha = entry['result_sha']
        write_paths = entry['write_paths']
        if (type(source_sha) is not str or _m._HEX40.fullmatch(source_sha) is None or
                type(result_sha) is not str or _m._HEX40.fullmatch(result_sha) is None or
                source_sha == result_sha or
                type(write_paths) is not list or not write_paths or
                len(write_paths) > _m._TASK_PATH_LIMIT or
                any(not _relative_path(path) for path in write_paths) or
                len(set(write_paths)) != len(write_paths)):
            _stop('RESULT_SCOPE_MISMATCH')
        normalized.append({'source_sha': source_sha, 'result_sha': result_sha,
                           'write_paths': list(write_paths)})
    for index, entry in enumerate(normalized):
        expected = (normalized[index + 1]['source_sha']
                    if index + 1 < len(normalized) else bound_source_sha)
        if entry['result_sha'] != expected:
            _stop('RESULT_SCOPE_MISMATCH')
    for entry in normalized:
        try:
            _, status = _git(
                root, ('merge-base', '--is-ancestor', entry['source_sha'],
                       entry['result_sha']),
                limit=4096, allow_exit_1=True, return_exit_code=True)
        except LLMToolError:
            _stop('RESULT_SCOPE_MISMATCH')
        if status != 0:
            _stop('RESULT_SCOPE_MISMATCH')
    return normalized


def _commit_paths(root, source_sha, result_sha, trusted_base_sha):
    """Return (commit, attributed paths, trusted merge second parent) per commit.

    This mirrors the single-Task attribution in ``_developer_changed_paths``:
    trusted main history is inherited and skipped, a trusted merge path whose
    blob equals its second parent is not Developer work, and merge resolution
    or conflict paths are always attributed (including the guidance refresh).
    """
    if trusted_base_sha is None:
        trusted_base_sha = source_sha
    if type(trusted_base_sha) is not str or _m._HEX40.fullmatch(trusted_base_sha) is None:
        _stop('GIT_INSPECTION_FAILED')

    commits = _git(root, ('rev-list', '--reverse', '--topo-order',
                          source_sha + '..' + result_sha), limit=1024 * 1024)
    try:
        commit_ids = [item for item in commits.decode('ascii').splitlines() if item]
    except UnicodeError:
        _stop('GIT_INSPECTION_FAILED')
    if len(commit_ids) > 4096:
        _stop('RESULT_DIFF_INVALID')

    listed_commits = set(commit_ids)
    commit_parents = {}
    for commit in commit_ids:
        raw = _git(root, ('rev-list', '--parents', '-n', '1', commit), limit=4096)
        fields = raw.decode('ascii').split()
        if len(fields) < 2:
            _stop('SOURCE_NOT_ANCESTOR')
        commit_parents[commit] = fields[1:]

    current = result_sha
    while current != source_sha:
        if current not in listed_commits:
            _stop('SOURCE_NOT_ANCESTOR')
        current = commit_parents[current][0]

    inherited = set()
    merge_parents = {}
    merge_resolution_paths = {}
    for commit in commit_ids:
        fields = [commit, *commit_parents[commit]]
        if len(fields) < 3:
            continue
        second_parent = fields[2]
        _m._require_trusted_base(root, trusted_base_sha)
        if not _m._is_ancestor(root, second_parent, trusted_base_sha):
            continue
        merge_parents[commit] = second_parent
        history = _git(root, ('rev-list', '--max-count=4097', second_parent,
                              '^' + source_sha), limit=1024 * 1024)
        inherited_commits = history.decode('ascii').splitlines()
        if len(inherited_commits) > 4096:
            _stop('RESULT_DIFF_INVALID')
        inherited.update(inherited_commits)
        if len(fields) > 3:
            continue
        merge_resolution_paths[commit] = _m._merge_resolution_paths(
            root, commit, fields[1], second_parent)

    attributed = []
    seen = set()
    for commit in commit_ids:
        if commit in inherited:
            continue
        fields = [commit, *commit_parents[commit]]
        if len(fields) > 3 and commit in merge_parents:
            _stop('RESULT_DIFF_INVALID')
        first_parent = fields[1]
        if len(fields) == 2 and _m._guidance_refresh_commit(root, commit, first_parent):
            continue
        changed = _git(root, ('diff-tree', '--no-commit-id', '--name-only', '-r', '-z',
                              '--no-renames', first_parent, commit, '--'),
                       limit=1024 * 1024)
        try:
            paths = [item for item in changed.decode('utf-8', 'strict').split('\x00') if item]
        except UnicodeError:
            _stop('GIT_INSPECTION_FAILED')
        second_parent = merge_parents.get(commit)
        commit_paths = set()
        for path in paths:
            if (second_parent is not None and
                    path not in merge_resolution_paths.get(commit, set()) and
                    _m._tree_blob(root, commit, path) ==
                    _m._tree_blob(root, second_parent, path)):
                continue
            commit_paths.add(path)
        commit_paths.update(merge_resolution_paths.get(commit, set()))
        seen.update(commit_paths)
        if len(seen) > 128:
            _stop('RESULT_DIFF_INVALID')
        attributed.append((commit, commit_paths, second_parent))
    return attributed


def _apply_stacked_chain(root, stacked_chain, artifact_segments, bound_source_sha,
                         bound_result_sha, bound_write_paths, trusted_base_sha=None):
    """Validate and scope-check a chain, refusing when segments are also given."""
    if artifact_segments is not None:
        _stop('DEVELOPER_ARTIFACT_CHAIN_INVALID')
    normalized = _normalize_stacked_chain(root, stacked_chain, bound_source_sha)
    _stacked_scope_check(root, normalized, bound_source_sha, bound_result_sha,
                         bound_write_paths, trusted_base_sha)
    return normalized


def _stacked_artifact_scope(root, stacked_chain, bound_source_sha,
                            bound_write_paths, tip, trusted_base_sha):
    """Return (allowed write paths, Developer scope) for a stacked artifact.

    The full range already passed ``_stacked_scope_check``, so only a trusted
    main merge in the bound Task's range can attribute a path from an earlier
    Task. The artifact check therefore compares its scope with the same union
    the merge check allows, never with a single Task's write paths.
    """
    normalized = _normalize_stacked_chain(root, stacked_chain, bound_source_sha)
    allowed = set(bound_write_paths)
    for entry in normalized:
        allowed.update(entry['write_paths'])
    return sorted(allowed), _m._developer_changed_paths(
        root, bound_source_sha, tip, trusted_base_sha)


def _stacked_chain_evidence(root, stacked_chain, bound_source_sha):
    """Return the evidence keys that bind the checked chain to this result."""
    return {'stacked_chain_used': True,
            'stacked_chain': _normalize_stacked_chain(
                root, stacked_chain, bound_source_sha)}


def _stacked_scope_check(root, normalized_chain, bound_source_sha, bound_result_sha,
                         bound_write_paths, trusted_base_sha=None):
    """Refuse unless each Task's commits and the bound merge stay in scope.

    A non-merge commit may only touch its own Task's write paths. A two-parent
    merge of trusted main in the bound Task's range may also touch the union of
    every chain Task's write paths. Any other merge gets no union.
    """
    try:
        _, status = _git(root, ('merge-base', '--is-ancestor',
                                bound_source_sha, bound_result_sha),
                         limit=4096, allow_exit_1=True, return_exit_code=True)
    except LLMToolError:
        _stop('RESULT_SCOPE_MISMATCH')
    if status != 0:
        _stop('RESULT_SCOPE_MISMATCH')

    union = set(bound_write_paths)
    for entry in normalized_chain:
        union.update(entry['write_paths'])
    bound_paths = set(bound_write_paths)

    for entry in normalized_chain:
        allowed = set(entry['write_paths'])
        for _commit, paths, _second_parent in _commit_paths(
                root, entry['source_sha'], entry['result_sha'], trusted_base_sha):
            if not paths.issubset(allowed):
                _stop('RESULT_SCOPE_MISMATCH')

    for _commit, paths, second_parent in _commit_paths(
            root, bound_source_sha, bound_result_sha, trusted_base_sha):
        allowed = union if second_parent is not None else bound_paths
        if not paths.issubset(allowed):
            _stop('RESULT_SCOPE_MISMATCH')


def _resolve_test_target(root, path, kind):
    """Validate an admitted test path and return (start_dir, filename)."""
    if not _relative_path(path):
        _stop('TEST_PATH_INVALID')
    target = Path(root).joinpath(*path.split('/'))
    try:
        target_stat = target.lstat()
    except OSError:
        _stop('TEST_PATH_INVALID')
    if not stat.S_ISREG(target_stat.st_mode):
        _stop('TEST_PATH_INVALID')
    parent = Path(path).parent.as_posix()
    filename = Path(path).name
    if kind in ('unittest', 'script'):
        # unittest discover walks subdirectories recursively; refuse if any
        # other file with the same name sits under start_dir so discovery can
        # never report a result from another file.
        start_path = (Path(root) if parent == '.'
                      else Path(root).joinpath(*parent.split('/')))
        target_real = os.path.realpath(target)
        for dirpath, dirnames, filenames in os.walk(start_path):
            dirnames[:] = [d for d in dirnames if d != '.git']
            if filename in filenames:
                candidate_real = os.path.realpath(
                    os.path.join(dirpath, filename))
                if candidate_real != target_real:
                    _stop('TEST_PATH_INVALID')
    return parent, filename


def _run_registered_tests(root, source_sha, eligible, *, home, pycache=None):
    per_module_timeout, total_timeout = _m._local_test_timeouts()
    evidence = []
    run_deadline = time.monotonic() + total_timeout
    for path, kind in eligible:
        if time.monotonic() >= run_deadline:
            _stop('TEST_RUNNER_TIMEOUT')
        start_dir, filename = _resolve_test_target(root, path, kind)
        env = {'PATH': os.path.dirname(sys.executable) + ':/usr/bin:/bin',
               'LC_ALL': 'C', 'HOME': home,
               'PYTHONPATH': str(Path(root) / 'tools' / 'llmtool'),
               'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': home}
        if pycache is not None:
            env['PYTHONPYCACHEPREFIX'] = pycache
        if kind == 'unittest':
            argv = [sys.executable, '-B', '-m', 'unittest', 'discover',
                    '-s', start_dir, '-p', filename]
        elif kind == 'script':
            argv = [sys.executable, '-I', '-B', '-m', 'unittest', 'discover',
                    '-s', start_dir, '-p', filename]
        else:
            argv = [sys.executable, '-I', '-B', path]
        try:
            process = _m.subprocess.Popen(
                argv, cwd=root, env=env,
                stdin=_m.subprocess.DEVNULL, stdout=_m.subprocess.PIPE,
                stderr=_m.subprocess.STDOUT, shell=False, close_fds=True,
                start_new_session=True)
        except OSError:
            _stop('TEST_RUNNER_UNAVAILABLE')
        output = bytearray()
        deadline = min(time.monotonic() + per_module_timeout, run_deadline)
        selector = selectors.DefaultSelector()
        started = time.monotonic()
        try:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _stop('TEST_RUNNER_TIMEOUT')
                for key, _ in selector.select(min(remaining, .1)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        if len(output) + len(chunk) > _m._TEST_OUTPUT_LIMIT:
                            _stop('TEST_OUTPUT_TOO_LARGE')
                        output.extend(chunk)
            code = process.wait(timeout=max(0, deadline - time.monotonic()))
        except _m.subprocess.TimeoutExpired:
            _stop('TEST_RUNNER_TIMEOUT')
        finally:
            selector.close()
            if process.poll() is None:
                try:
                    os.killpg(process.pid, 9)
                except OSError:
                    process.kill()
                process.wait()
            process.stdout.close()
        item = {
            'path': path, 'source_sha': source_sha, 'exit_code': code,
            'output_sha256': hashlib.sha256(output).hexdigest(),
            'duration_ms': max(0, int((time.monotonic() - started) * 1000)),
            'command_sha256': hashlib.sha256(
                json.dumps(argv, separators=(',', ':')).encode()).hexdigest(),
        }
        ran = re.findall(rb'(?m)^Ran ([0-9]+) tests?\b', output)
        # A reported zero fails; a discovery kind always reports a count.
        zero_tests = bool(ran) and int(ran[-1]) == 0
        discovery = kind in ('unittest', 'script')
        if code or zero_tests or (discovery and not ran):
            item['output_excerpt'] = _m._test_excerpt(home, output, code)
            item['status'] = 'FAILED'
        else:
            item['status'] = 'PASSED'
        evidence.append(item)
    return evidence
