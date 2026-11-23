"""Refresh tools/llmtool/guidance-drift.json hashes for authorized Task guidance edits (#2497)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess


CONFIG_REL_PATH = 'tools/llmtool/guidance-drift.json'
GUIDANCE_FILES = frozenset(('AGENTS.md', 'CLAUDE.md'))
_HEX40 = re.compile(r'[0-9a-f]{40}')
_HEX64 = re.compile(r'[0-9a-f]{64}')


class GuidanceRefreshError(Exception):
    """Refusal raised when guidance hash refresh cannot proceed safely."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _default_git_runner(root, args):
    return subprocess.run(
        ['git', *args],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=30,
    )


def _git_bytes(git_runner, root, args, *, code='GUIDANCE_REFRESH_COMMAND_FAILED'):
    try:
        result = git_runner(root, args)
    except Exception:
        raise GuidanceRefreshError(code) from None
    if getattr(result, 'returncode', 1) != 0:
        raise GuidanceRefreshError(code)
    stdout = getattr(result, 'stdout', None)
    if type(stdout) is bytes:
        return stdout
    if type(stdout) is str:
        return stdout.encode('utf-8')
    raise GuidanceRefreshError(code)


def _git_text(git_runner, root, args, *, code='GUIDANCE_REFRESH_COMMAND_FAILED'):
    raw = _git_bytes(git_runner, root, args, code=code)
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        raise GuidanceRefreshError(code) from None


def _valid_repo_path(path):
    return (
        type(path) is str
        and bool(path)
        and not path.startswith('/')
        and '\\' not in path
        and not any(part in ('', '.', '..') for part in path.split('/'))
    )


def _valid_write_paths(write_paths):
    return (
        isinstance(write_paths, (list, tuple, set, frozenset))
        and bool(write_paths)
        and all(_valid_repo_path(path) for path in write_paths)
    )


def _valid_task_commits(task_commits):
    if not isinstance(task_commits, (list, tuple)):
        return False
    for item in task_commits:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return False
        commit, parents = item
        if type(commit) is not str or _HEX40.fullmatch(commit) is None:
            return False
        if not isinstance(parents, (list, tuple)):
            return False
        if any(type(parent) is not str or _HEX40.fullmatch(parent) is None for parent in parents):
            return False
    return True


def _commit_paths(git_runner, root, commit, parents):
    if not parents:
        raw = _git_text(
            git_runner, root,
            ['--no-replace-objects', 'ls-tree', '-r', '--name-only', '-z', commit],
        )
        return {path for path in raw.split('\0') if path}
    introduced = None
    for parent in parents:
        raw = _git_text(
            git_runner, root,
            ['--no-replace-objects', 'diff', '--no-renames', '--name-only', '-z', parent, commit],
        )
        changed = {path for path in raw.split('\0') if path}
        introduced = changed if introduced is None else introduced & changed
    return introduced or set()


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _reject_constant(_name):
    raise ValueError('non-finite JSON number')


def _load_config_bytes(raw_bytes):
    try:
        text = raw_bytes.decode('utf-8')
        config = json.loads(
            text,
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise GuidanceRefreshError('GUIDANCE_REFRESH_CONFIG_INVALID') from None

    if type(config) is not dict or set(config) != {'schema_version', 'hashes', 'codex_only'}:
        raise GuidanceRefreshError('GUIDANCE_REFRESH_CONFIG_INVALID')
    if type(config.get('schema_version')) is not int or config['schema_version'] != 1:
        raise GuidanceRefreshError('GUIDANCE_REFRESH_CONFIG_INVALID')

    hashes = config.get('hashes')
    if type(hashes) is not dict or set(hashes) != GUIDANCE_FILES:
        raise GuidanceRefreshError('GUIDANCE_REFRESH_CONFIG_INVALID')
    if any(type(value) is not str or _HEX64.fullmatch(value) is None for value in hashes.values()):
        raise GuidanceRefreshError('GUIDANCE_REFRESH_CONFIG_INVALID')

    exceptions = config.get('codex_only')
    if type(exceptions) is not list:
        raise GuidanceRefreshError('GUIDANCE_REFRESH_CONFIG_INVALID')
    seen = set()
    for entry in exceptions:
        if (
            type(entry) is not dict
            or set(entry) != {'value', 'reason'}
            or type(entry['value']) is not str
            or not entry['value']
            or '\n' in entry['value']
            or '\r' in entry['value']
            or type(entry['reason']) is not str
            or not entry['reason'].strip()
            or '\n' in entry['reason']
            or '\r' in entry['reason']
            or entry['value'] in seen
        ):
            raise GuidanceRefreshError('GUIDANCE_REFRESH_CONFIG_INVALID')
        seen.add(entry['value'])
    return config


def refresh_guidance_hashes(
    *,
    root,
    head,
    write_paths,
    task_commits,
    target_paths=None,
    git_runner=None,
    commit_message=None,
):
    """Refresh guidance-drift.json hashes for authorized guidance edits at exact head.

    Verifies that every guidance file to refresh is in ``write_paths`` and in
    the Task's own committed diff at ``head``. Refuses with
    ``GuidanceRefreshError`` and changes nothing otherwise.
    """
    if (
        not isinstance(root, (str, Path))
        or not str(root)
        or type(head) is not str
        or _HEX40.fullmatch(head) is None
        or not _valid_write_paths(write_paths)
        or not _valid_task_commits(task_commits)
    ):
        raise GuidanceRefreshError('GUIDANCE_REFRESH_INVALID_ARGS')

    runner = _default_git_runner if git_runner is None else git_runner
    actual_head = _git_text(runner, root, ['rev-parse', '--verify', 'HEAD']).strip()
    if actual_head != head:
        raise GuidanceRefreshError('GUIDANCE_REFRESH_HEAD_MISMATCH')
    if task_commits and head not in {commit for commit, _parents in task_commits}:
        raise GuidanceRefreshError('GUIDANCE_REFRESH_HEAD_MISMATCH')

    committed_paths = set()
    for commit, parents in task_commits:
        committed_paths.update(_commit_paths(runner, root, commit, parents))

    if target_paths is not None:
        if (
            not isinstance(target_paths, (list, tuple, set, frozenset))
            or any(type(path) is not str or path not in GUIDANCE_FILES for path in target_paths)
        ):
            raise GuidanceRefreshError('GUIDANCE_REFRESH_INVALID_ARGS')
        candidates = sorted(set(target_paths))
    else:
        candidates = sorted(path for path in committed_paths if path in GUIDANCE_FILES)

    if not candidates:
        return {'refreshed': False, 'head': head, 'updated': []}

    for path in candidates:
        if path not in write_paths:
            raise GuidanceRefreshError('GUIDANCE_REFRESH_OUT_OF_SCOPE')
        if path not in committed_paths:
            raise GuidanceRefreshError('GUIDANCE_REFRESH_NOT_IN_TASK_DIFF')

    tracked_dirty = _git_text(
        runner, root,
        ['diff', '--no-renames', '--name-only', '-z', 'HEAD', '--', CONFIG_REL_PATH],
    )
    untracked_dirty = _git_text(
        runner, root,
        ['ls-files', '--others', '--exclude-standard', '-z', '--', CONFIG_REL_PATH],
    )
    if tracked_dirty or untracked_dirty:
        raise GuidanceRefreshError('GUIDANCE_REFRESH_DIRTY_WORKTREE')

    raw_config = _git_bytes(
        runner, root,
        ['--no-replace-objects', 'cat-file', 'blob', f'{head}:{CONFIG_REL_PATH}'],
        code='GUIDANCE_REFRESH_CONFIG_MISSING',
    )
    config = _load_config_bytes(raw_config)

    updates = {}
    for path in candidates:
        blob = _git_bytes(
            runner, root,
            ['--no-replace-objects', 'cat-file', 'blob', f'{head}:{path}'],
            code='GUIDANCE_REFRESH_FILE_UNAVAILABLE',
        )
        digest = hashlib.sha256(blob).hexdigest()
        if config['hashes'][path] != digest:
            updates[path] = digest

    if not updates:
        return {'refreshed': False, 'head': head, 'updated': []}

    for path, digest in updates.items():
        config['hashes'][path] = digest

    config_file = Path(root) / CONFIG_REL_PATH
    try:
        config_file.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    except OSError:
        raise GuidanceRefreshError('GUIDANCE_REFRESH_COMMAND_FAILED') from None

    _git_bytes(runner, root, ['add', '--', CONFIG_REL_PATH])
    staged = _git_text(runner, root, ['diff', '--cached', '--no-renames', '--name-only', '-z', 'HEAD'])
    staged_paths = [path for path in staged.split('\0') if path]
    if staged_paths != [CONFIG_REL_PATH]:
        raise GuidanceRefreshError('GUIDANCE_REFRESH_COMMAND_FAILED')

    if isinstance(commit_message, (tuple, list)) and len(commit_message) == 2 and all(
        type(part) is str and part for part in commit_message
    ):
        commit_args = ['commit', '-m', commit_message[0], '-m', commit_message[1]]
    elif type(commit_message) is str and commit_message:
        commit_args = ['commit', '-m', commit_message]
    else:
        commit_args = ['commit', '-m', 'Refresh guidance-drift.json hash']

    _git_bytes(runner, root, commit_args)
    new_head = _git_text(runner, root, ['rev-parse', '--verify', 'HEAD']).strip()
    if _HEX40.fullmatch(new_head) is None:
        raise GuidanceRefreshError('GUIDANCE_REFRESH_COMMAND_FAILED')
    return {'refreshed': True, 'head': new_head, 'updated': sorted(updates)}


def is_refresh_content(parent_config_bytes, child_config_bytes, guidance_blobs):
    """True when child_config_bytes is exactly the refresh of parent_config_bytes.

    #2640: the publish step's own hash-refresh commit is not Developer work.
    It qualifies only when the new guidance-drift.json bytes are exactly what
    refresh_guidance_hashes writes for the parent: the parent config with each
    guidance file's hash set to the sha256 of that file in the parent tree, at
    least one hash changed, nothing else changed. guidance_blobs maps each
    guidance file to its bytes in the parent tree. Any doubt is False.
    """
    try:
        config = _load_config_bytes(parent_config_bytes)
    except GuidanceRefreshError:
        return False
    if type(guidance_blobs) is not dict or set(guidance_blobs) != GUIDANCE_FILES:
        return False
    changed = False
    for path, blob in guidance_blobs.items():
        if type(blob) is not bytes:
            return False
        digest = hashlib.sha256(blob).hexdigest()
        if config['hashes'][path] != digest:
            config['hashes'][path] = digest
            changed = True
    return changed and child_config_bytes == (json.dumps(config, indent=2) + '\n').encode('utf-8')
