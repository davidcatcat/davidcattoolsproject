"""Merge-train candidate build and proof (#1182 T2).

build_candidate merges an ordered list of member heads onto a base commit
using only clean two-parent merges. It adds objects to the repository object
database and nothing else: it never runs checkout, merge, reset, update-ref,
push or fetch, and never changes the worktree, the index, HEAD or any ref.

train_delta_verified proves that a train head is exactly the base plus those
clean merges, in order, and nothing else. Every doubt returns False.

Git reads, merge-tree conflict paths, merge resolution paths and the graft
refusal are the task_completion helpers; this module does not copy them.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile

from . import task_completion
from .errors import LLMToolError

_HEX40 = task_completion._HEX40
_KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._#/-]{0,95}\Z')
_MEMBER_LIMIT = 64
_COMMIT_LIMIT = 1024 * 1024
# Fixed identity for train commits; no user or command data is used.
_IDENTITY = {
    'GIT_AUTHOR_NAME': 'llmtool merge train',
    'GIT_AUTHOR_EMAIL': 'llmtool-merge-train@invalid',
    'GIT_COMMITTER_NAME': 'llmtool merge train',
    'GIT_COMMITTER_EMAIL': 'llmtool-merge-train@invalid',
}

TRAIN_MEMBER_CONFLICT = 'TRAIN_MEMBER_CONFLICT'
TRAIN_MEMBER_UNAVAILABLE = 'TRAIN_MEMBER_UNAVAILABLE'
TRAIN_MEMBER_ALREADY_MERGED = 'TRAIN_MEMBER_ALREADY_MERGED'
TRAIN_TOO_FEW_MEMBERS = 'TRAIN_TOO_FEW_MEMBERS'
TRAIN_BASE_UNAVAILABLE = 'TRAIN_BASE_UNAVAILABLE'
TRAIN_MEMBERS_INVALID = 'TRAIN_MEMBERS_INVALID'
TRAIN_GRAFTS_UNSUPPORTED = 'TRAIN_GRAFTS_UNSUPPORTED'
TRAIN_BUILD_FAILED = 'TRAIN_BUILD_FAILED'
TRAIN_CANDIDATE_UNVERIFIED = 'TRAIN_CANDIDATE_UNVERIFIED'


def _is_commit(root, sha):
    """Return whether sha is a lowercase 40-hex commit object in the repository."""
    if type(sha) is not str or _HEX40.fullmatch(sha) is None:
        return False
    try:
        kind = task_completion._git(root, ('cat-file', '-t', sha), limit=64)
    except LLMToolError:
        return False
    return kind == b'commit\n'


def _commit_header(root, sha):
    """Return (tree, parents) read from the raw commit object."""
    raw = task_completion._git(root, ('cat-file', 'commit', sha), limit=_COMMIT_LIMIT)
    header, separator, _message = raw.partition(b'\n\n')
    if not separator:
        task_completion._stop('GIT_INSPECTION_FAILED')
    lines = header.split(b'\n')
    if not lines[0].startswith(b'tree '):
        task_completion._stop('GIT_INSPECTION_FAILED')
    trees, parents = [], []
    for line in lines:
        # Continuation lines of multi-line headers start with a space.
        if line.startswith(b'tree '):
            trees.append(line[5:])
        elif line.startswith(b'parent '):
            parents.append(line[7:])
    try:
        trees = [value.decode('ascii', 'strict') for value in trees]
        parents = [value.decode('ascii', 'strict') for value in parents]
    except UnicodeError:
        task_completion._stop('GIT_INSPECTION_FAILED')
    if (len(trees) != 1 or
            any(_HEX40.fullmatch(value) is None for value in trees + parents)):
        task_completion._stop('GIT_INSPECTION_FAILED')
    return trees[0], parents


def _auto_merge(root, first_parent, second_parent):
    """Return (tree, conflict paths) of the automatic merge, read privately."""
    raw = task_completion._merge_tree_conflict_paths_raw(root, first_parent, second_parent)
    try:
        fields = raw.decode('utf-8', 'strict').split('\x00')
    except UnicodeError:
        task_completion._stop('GIT_INSPECTION_FAILED')
    if (len(fields) < 2 or _HEX40.fullmatch(fields[0]) is None or fields[-1] != '' or
            any(not path for path in fields[1:-1])):
        task_completion._stop('GIT_INSPECTION_FAILED')
    return fields[0], tuple(fields[1:-1])


def _write_merge(root, tip, head, tree, key):
    """Add the clean merge commit of tip and head to the object database.

    The commands run in a private bare repository with no merge config, no
    attributes, no index and no refs that matter; only its object directory
    is the repository's. So the merge and commit only add objects.
    """
    objects = task_completion._git(
        root, ('rev-parse', '--path-format=absolute', '--git-path', 'objects'),
        limit=4096).decode('utf-8', 'strict').strip()
    if not objects or not os.path.isabs(objects) or not os.path.isdir(objects):
        task_completion._stop('GIT_INSPECTION_FAILED')
    message = 'llmtool merge train member ' + key
    with tempfile.TemporaryDirectory(prefix='llmtool-merge-train-') as temporary:
        private = Path(temporary).resolve()
        (private / 'objects').mkdir(mode=0o700)
        (private / 'refs').mkdir(mode=0o700)
        (private / 'HEAD').write_text('ref: refs/heads/llmtool-merge-train\n',
                                      encoding='ascii')
        (private / 'config').write_text(
            '[core]\n\trepositoryformatversion = 0\n\tbare = true\n', encoding='ascii')
        env = {'GIT_DIR': str(private), 'GIT_OBJECT_DIRECTORY': objects, **_IDENTITY}
        written = task_completion._git(str(private), (
            'merge-tree', '--write-tree', '--name-only', '--no-messages', '-z',
            tip, head), limit=4096, env_overrides=env)
        if written != (tree + '\x00').encode('ascii'):
            task_completion._stop('GIT_INSPECTION_FAILED')
        out = task_completion._git(str(private), (
            'commit-tree', tree, '-p', tip, '-p', head, '-m', message),
            limit=4096, env_overrides=env)
    try:
        commit = out.decode('ascii', 'strict')
    except UnicodeError:
        task_completion._stop('GIT_INSPECTION_FAILED')
    if not commit.endswith('\n') or _HEX40.fullmatch(commit[:-1]) is None:
        task_completion._stop('GIT_INSPECTION_FAILED')
    commit = commit[:-1]
    if not _is_commit(root, commit) or _commit_header(root, commit) != (tree, [tip, head]):
        task_completion._stop('GIT_INSPECTION_FAILED')
    return commit


def _members_valid(members):
    if type(members) not in (list, tuple) or len(members) > _MEMBER_LIMIT:
        return False
    keys = []
    for member in members:
        if type(member) not in (list, tuple) or len(member) != 2:
            return False
        key = member[0]
        if type(key) is not str or _KEY.fullmatch(key) is None:
            return False
        keys.append(key)
    return len(set(keys)) == len(keys)


def build_candidate(root, base_sha, members):
    """Build a merge-train candidate from base_sha and ordered (key, head) members.

    Returns {'head', 'merged', 'excluded', 'codes'}. head is the final tip, or
    None when the train is not usable; codes then names the reason.
    """
    result = {'head': None, 'merged': [], 'excluded': [], 'codes': []}
    if not _members_valid(members):
        result['codes'] = [TRAIN_MEMBERS_INVALID]
        return result
    if not _is_commit(root, base_sha):
        result['codes'] = [TRAIN_BASE_UNAVAILABLE]
        return result
    try:
        task_completion._reject_legacy_grafts(root)
    except Exception:
        result['codes'] = [TRAIN_GRAFTS_UNSUPPORTED]
        return result
    merged, excluded, merged_heads = result['merged'], result['excluded'], []
    tip, seen = base_sha, set()
    try:
        for key, head in members:
            if not _is_commit(root, head):
                excluded.append({'key': key, 'code': TRAIN_MEMBER_UNAVAILABLE})
                continue
            if head in seen:
                excluded.append({'key': key, 'code': TRAIN_MEMBER_ALREADY_MERGED})
                continue
            seen.add(head)
            if task_completion._is_ancestor(root, head, tip):
                excluded.append({'key': key, 'code': TRAIN_MEMBER_ALREADY_MERGED})
                continue
            tree, conflicts = _auto_merge(root, tip, head)
            if conflicts:
                excluded.append({'key': key, 'code': TRAIN_MEMBER_CONFLICT})
                continue
            tip = _write_merge(root, tip, head, tree, key)
            merged.append(key)
            merged_heads.append(head)
    except Exception:
        result['codes'] = [TRAIN_BUILD_FAILED]
        return result
    if len(merged) < 2:
        result['codes'] = [TRAIN_TOO_FEW_MEMBERS]
        return result
    if not train_delta_verified(root, base_sha, merged_heads, tip):
        result['codes'] = [TRAIN_CANDIDATE_UNVERIFIED]
        return result
    result['head'] = tip
    return result


def _verify(root, base_sha, member_heads, train_head):
    if type(member_heads) not in (list, tuple):
        return False
    heads = list(member_heads)
    if not heads or len(heads) > _MEMBER_LIMIT:
        return False
    if any(type(sha) is not str or _HEX40.fullmatch(sha) is None
           for sha in [base_sha, train_head, *heads]):
        return False
    if len(set(heads)) != len(heads):
        return False
    # A graft file could rewrite the parents that the walk below trusts.
    task_completion._reject_legacy_grafts(root)
    if not _is_commit(root, base_sha) or not _is_commit(root, train_head):
        return False
    current = train_head
    for expected in reversed(heads):
        if current == base_sha:
            return False
        tree, parents = _commit_header(root, current)
        if len(parents) != 2 or parents[1] != expected:
            return False
        first, second = parents
        if task_completion._is_ancestor(root, second, first):
            return False
        auto_tree, conflicts = _auto_merge(root, first, second)
        if conflicts or auto_tree != tree:
            return False
        if task_completion._merge_resolution_paths(root, current, first, second):
            return False
        current = first
    return current == base_sha


def train_delta_verified(root, base_sha, member_heads, train_head):
    """Return True only when train_head is base_sha plus exactly the clean
    two-parent merges of member_heads, in order, and nothing else."""
    try:
        return _verify(root, base_sha, member_heads, train_head) is True
    except Exception:
        return False
