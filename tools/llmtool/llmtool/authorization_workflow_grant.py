"""Evaluate an exact workflow-file grant for a Task Issue and mode.

The grant source is the reviewed in-repository rule file
``tools/llmtool/workflow-grants.json`` at the caller-supplied Task base commit.
The file is read from the Git object store, never from the working tree, so an
uncommitted edit cannot widen a grant. This module only answers a grant
question; it has no caller yet and grants no authority beyond an exact Issue,
mode and workflow-file path match.
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

MAX_GRANTS_BYTES = 65536
MAX_PATH_BYTES = 1024
MAX_PATHS_PER_GRANT = 32
GIT_TIMEOUT_SECONDS = 30
GRANT_FILE = 'tools/llmtool/workflow-grants.json'
MODES = frozenset(('read', 'write'))
_ENTRY_FIELDS = frozenset(('issue', 'owner_record_url', 'mode', 'paths'))
_OWNER_RECORD = re.compile(r'https://[A-Za-z0-9.-]+/[^\s]+')
_BASE_COMMIT = re.compile(r'[0-9a-f]{40}')


class WorkflowGrantCode(str, Enum):
    GRANTED = 'GRANTED'
    GRANT_FILE_MISSING = 'GRANT_FILE_MISSING'
    GRANT_FILE_MALFORMED = 'GRANT_FILE_MALFORMED'
    GRANT_BASE_COMMIT_INVALID = 'GRANT_BASE_COMMIT_INVALID'
    GRANT_SOURCE_UNAVAILABLE = 'GRANT_SOURCE_UNAVAILABLE'
    GRANT_ISSUE_INVALID = 'GRANT_ISSUE_INVALID'
    GRANT_MODE_INVALID = 'GRANT_MODE_INVALID'
    GRANT_PATH_INVALID = 'GRANT_PATH_INVALID'
    GRANT_PATH_CONFUSION = 'GRANT_PATH_CONFUSION'
    GRANT_PATH_PREFIX = 'GRANT_PATH_PREFIX'
    GRANT_PATH_SYMLINK = 'GRANT_PATH_SYMLINK'
    GRANT_OWNER_RECORD_MISSING = 'GRANT_OWNER_RECORD_MISSING'
    GRANT_OWNER_RECORD_INVALID = 'GRANT_OWNER_RECORD_INVALID'
    GRANT_NOT_FOUND = 'GRANT_NOT_FOUND'


@dataclass(frozen=True)
class WorkflowGrantDecision:
    allowed: bool
    code: WorkflowGrantCode


@dataclass(frozen=True)
class _Grant:
    issue: int
    mode: str
    paths: tuple[str, ...]


class _Refusal(Exception):
    def __init__(self, code):
        super().__init__(code.value)
        self.code = code


def _grant_list_path() -> Path:
    return Path(__file__).resolve().parents[1] / 'workflow-grants.json'


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _safe_text(value, limit):
    if type(value) is not str or not value:
        return False
    try:
        if len(value) > limit or len(value.encode('utf-8')) > limit:
            return False
    except UnicodeError:
        return False
    return not any(ord(char) < 32 or 127 <= ord(char) <= 159 or
                   unicodedata.category(char) == 'Cf' for char in value)


def _workflow_parts(path):
    """Return the components of an exact workflow file, or None.

    GitHub runs only top-level ``.github/workflows/*.yml`` and ``*.yaml``
    files, so a nested path or a name without that extension is not a
    workflow file and is refused rather than treated as one.
    """
    if not _safe_text(path, MAX_PATH_BYTES):
        return None
    if unicodedata.normalize('NFC', path) != path:
        return None
    if path.startswith('/') or path.endswith('/') or '\\' in path:
        return None
    parts = path.split('/')
    if len(parts) != 3:
        return None
    if parts[0] != '.github' or parts[1] != 'workflows':
        return None
    for part in parts:
        if (part in ('', '.', '..') or part.startswith('-') or
                any(not (char.isalnum() or char in '._-') for char in part)):
            return None
    name = parts[2]
    if name.endswith('.yml'):
        stem = name[:-4]
    elif name.endswith('.yaml'):
        stem = name[:-5]
    else:
        return None
    if not stem or stem.startswith('.'):
        return None
    return tuple(parts)


def _collision_key(path):
    return unicodedata.normalize('NFC', path).casefold()


def _is_component_prefix(shorter, longer):
    left = shorter.split('/')
    right = longer.split('/')
    return len(left) < len(right) and right[:len(left)] == left


def _has_symlink(root, path):
    current = Path(root)
    try:
        if current.is_symlink():
            return True
        for part in path.split('/'):
            current = current / part
            if current.is_symlink():
                return True
    except OSError:
        return True
    return False


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _owner_record_ok(value):
    return type(value) is str and _OWNER_RECORD.fullmatch(value) is not None


def _git(root, *arguments):
    return subprocess.run(
        ['git', '-C', str(root), *arguments],
        check=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT_SECONDS)


def _source_bytes(root, base_commit):
    """Read the rule file blob at the Task base commit, never the worktree."""
    try:
        kind = _git(root, 'cat-file', '-t', base_commit + '^{commit}')
        if kind.returncode != 0 or kind.stdout.strip() != b'commit':
            raise _Refusal(WorkflowGrantCode.GRANT_BASE_COMMIT_INVALID)
        blob = _git(root, 'cat-file', 'blob', f'{base_commit}:{GRANT_FILE}')
    except (OSError, subprocess.SubprocessError):
        raise _Refusal(WorkflowGrantCode.GRANT_SOURCE_UNAVAILABLE) from None
    if blob.returncode != 0:
        raise _Refusal(WorkflowGrantCode.GRANT_FILE_MISSING)
    return blob.stdout


def _parse_grants(raw):
    if type(raw) is not bytes or len(raw) > MAX_GRANTS_BYTES:
        raise _Refusal(WorkflowGrantCode.GRANT_FILE_MALFORMED)
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise _Refusal(WorkflowGrantCode.GRANT_FILE_MALFORMED) from None
    if (type(value) is not dict or set(value) != frozenset(('schema_version', 'grants')) or
            type(value.get('schema_version')) is not int or value['schema_version'] != 1):
        raise _Refusal(WorkflowGrantCode.GRANT_FILE_MALFORMED)
    entries = value['grants']
    if type(entries) is not list:
        raise _Refusal(WorkflowGrantCode.GRANT_FILE_MALFORMED)
    grants = []
    seen = set()
    for entry in entries:
        if type(entry) is not dict or not set(entry) <= _ENTRY_FIELDS:
            raise _Refusal(WorkflowGrantCode.GRANT_FILE_MALFORMED)
        if 'owner_record_url' not in entry or entry['owner_record_url'] in (None, ''):
            raise _Refusal(WorkflowGrantCode.GRANT_OWNER_RECORD_MISSING)
        if set(entry) != _ENTRY_FIELDS:
            raise _Refusal(WorkflowGrantCode.GRANT_FILE_MALFORMED)
        if not _owner_record_ok(entry['owner_record_url']):
            raise _Refusal(WorkflowGrantCode.GRANT_OWNER_RECORD_INVALID)
        issue = entry['issue']
        if type(issue) is not int or issue < 1:
            raise _Refusal(WorkflowGrantCode.GRANT_FILE_MALFORMED)
        mode = entry['mode']
        if type(mode) is not str or mode not in MODES:
            raise _Refusal(WorkflowGrantCode.GRANT_FILE_MALFORMED)
        paths = entry['paths']
        if type(paths) is not list or not 1 <= len(paths) <= MAX_PATHS_PER_GRANT:
            raise _Refusal(WorkflowGrantCode.GRANT_FILE_MALFORMED)
        keys = []
        for path in paths:
            if _workflow_parts(path) is None:
                raise _Refusal(WorkflowGrantCode.GRANT_FILE_MALFORMED)
            keys.append(_collision_key(path))
        if len(set(keys)) != len(keys):
            raise _Refusal(WorkflowGrantCode.GRANT_FILE_MALFORMED)
        for path in paths:
            key = (issue, mode, path)
            if key in seen:
                raise _Refusal(WorkflowGrantCode.GRANT_FILE_MALFORMED)
            seen.add(key)
        grants.append(_Grant(issue, mode, tuple(paths)))
    return tuple(grants)


def evaluate_workflow_grant(*, base_commit, issue, path, mode, root=None):
    """Return an allowed decision only for one exact Issue, mode and path match.

    ``base_commit`` is the Task base commit: the rule file is read from that
    commit, so the answer cannot be changed by an uncommitted or later edit.
    """
    if type(issue) is not int or issue < 1:
        return WorkflowGrantDecision(False, WorkflowGrantCode.GRANT_ISSUE_INVALID)
    if type(mode) is not str or mode not in MODES:
        return WorkflowGrantDecision(False, WorkflowGrantCode.GRANT_MODE_INVALID)
    if _workflow_parts(path) is None:
        return WorkflowGrantDecision(False, WorkflowGrantCode.GRANT_PATH_INVALID)
    if (type(base_commit) is not str or
            _BASE_COMMIT.fullmatch(base_commit) is None):
        return WorkflowGrantDecision(
            False, WorkflowGrantCode.GRANT_BASE_COMMIT_INVALID)
    try:
        grants = _parse_grants(_source_bytes(
            _repository_root() if root is None else root, base_commit))
    except _Refusal as refusal:
        return WorkflowGrantDecision(False, refusal.code)
    if _has_symlink(_repository_root() if root is None else root, path):
        return WorkflowGrantDecision(False, WorkflowGrantCode.GRANT_PATH_SYMLINK)
    key = _collision_key(path)
    for grant in grants:
        if grant.issue == issue and grant.mode == mode and path in grant.paths:
            return WorkflowGrantDecision(True, WorkflowGrantCode.GRANTED)
    for grant in grants:
        if grant.issue != issue or grant.mode != mode:
            continue
        if any(_collision_key(item) == key for item in grant.paths):
            return WorkflowGrantDecision(False, WorkflowGrantCode.GRANT_PATH_CONFUSION)
        if any(_is_component_prefix(item, path) or _is_component_prefix(path, item)
               for item in grant.paths):
            return WorkflowGrantDecision(False, WorkflowGrantCode.GRANT_PATH_PREFIX)
    return WorkflowGrantDecision(False, WorkflowGrantCode.GRANT_NOT_FOUND)
