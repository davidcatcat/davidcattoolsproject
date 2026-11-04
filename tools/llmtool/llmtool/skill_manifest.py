"""Resolve governance files from an immutable Git commit."""

import hashlib
import json
import os
import re
import subprocess


class SkillManifestError(Exception):
    """A safe, code-only failure while building a skill manifest."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


SKILL_MANIFEST_COMMIT_INVALID = 'SKILL_MANIFEST_COMMIT_INVALID'
SKILL_MANIFEST_PATH_MISSING = 'SKILL_MANIFEST_PATH_MISSING'
SKILL_MANIFEST_NOT_FILE = 'SKILL_MANIFEST_NOT_FILE'
SKILL_MANIFEST_NOT_UTF8 = 'SKILL_MANIFEST_NOT_UTF8'
SKILL_MANIFEST_TOO_LARGE = 'SKILL_MANIFEST_TOO_LARGE'
SKILL_MANIFEST_ROLE_INVALID = 'SKILL_MANIFEST_ROLE_INVALID'
SKILL_MANIFEST_GATE_INVALID = 'SKILL_MANIFEST_GATE_INVALID'

MAX_SKILL_FILE_SIZE = 1024 * 1024
GIT_READ_TIMEOUT_SECONDS = 10

_BASE_PATHS = (
    'AGENTS.md',
    'skills/ai-team-delivery/SKILL.md',
)
_CLAUDE_BASE_PATHS = (
    'CLAUDE.md',
    'skills/ai-team-delivery/SKILL.md',
)
_PM_PATHS = (
    'skills/ai-team-delivery/pm-continuation.md',
    'skills/ai-team-delivery/pm-continuation-reference.md',
)
_REVIEW_PATHS = (
    'skills/review-change/SKILL.md',
    'skills/ai-team-delivery/formal-verdict.md',
)
_GIT_PATHS = ('skills/safe-git/SKILL.md',)
_MANIFEST_TABLE = {
    'pm': {
        'dispatch': _BASE_PATHS + _PM_PATHS,
        'develop': _BASE_PATHS + _PM_PATHS,
        'review': _BASE_PATHS + _PM_PATHS + _REVIEW_PATHS,
        'complete': _BASE_PATHS + _PM_PATHS,
        'publish': _BASE_PATHS + _PM_PATHS + _GIT_PATHS,
        'merge': _BASE_PATHS + _PM_PATHS + _GIT_PATHS,
    },
    'developer': {
        'dispatch': _BASE_PATHS,
        'develop': _BASE_PATHS,
        'review': _BASE_PATHS + _REVIEW_PATHS,
        'complete': _BASE_PATHS,
        'publish': _BASE_PATHS + _GIT_PATHS,
        'merge': _BASE_PATHS + _GIT_PATHS,
    },
    'reviewer': {
        'dispatch': _BASE_PATHS,
        'develop': _BASE_PATHS,
        'review': _BASE_PATHS + _REVIEW_PATHS,
        'complete': _BASE_PATHS,
        'publish': _BASE_PATHS + _GIT_PATHS,
        'merge': _BASE_PATHS + _GIT_PATHS,
    },
    'high-debug': {
        'dispatch': _BASE_PATHS,
        'develop': _BASE_PATHS,
        'review': _BASE_PATHS + _REVIEW_PATHS,
        'complete': _BASE_PATHS,
        'publish': _BASE_PATHS + _GIT_PATHS,
        'merge': _BASE_PATHS + _GIT_PATHS,
    },
}
_COMMIT_RE = re.compile(r'^[0-9a-fA-F]{40}$')


def _raise(code):
    raise SkillManifestError(code)


def resolve(role, gate):
    """Return the sorted required path tuple for one role and gate."""
    if type(role) is not str or role not in _MANIFEST_TABLE:
        _raise(SKILL_MANIFEST_ROLE_INVALID)
    gates = _MANIFEST_TABLE[role]
    if type(gate) is not str or gate not in gates:
        _raise(SKILL_MANIFEST_GATE_INVALID)
    return tuple(sorted(gates[gate]))


def _run_git(git_runner, repo_root, args):
    """Run one bounded Git read, suppressing stderr from safe errors."""
    try:
        # Environment variables such as GIT_DIR and GIT_WORK_TREE can redirect
        # this read away from repo_root. Do not inherit any Git-specific state.
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith('GIT_')
        }
        result = git_runner(
            [
                'git', '--no-replace-objects', '-c',
                'core.useReplaceRefs=false',
            ] + list(args),
            cwd=repo_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=GIT_READ_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    return result


def _stdout(result):
    try:
        value = getattr(result, 'stdout', None)
        if type(value) is bytes:
            return value
        if type(value) is str:
            return value.encode('utf-8')
    except Exception:
        pass
    return b''


def _valid_object_id(value):
    return (
        type(value) is bytes
        and re.fullmatch(rb'[0-9a-fA-F]{40}', value) is not None
    )


def _read_blob(git_runner, repo_root, commit, path):
    tree = _run_git(
        git_runner, repo_root,
        ['ls-tree', '--full-tree', '-z', commit, '--', path],
    )
    if tree is None or getattr(tree, 'returncode', 1) != 0:
        _raise(SKILL_MANIFEST_PATH_MISSING)

    records = [record for record in _stdout(tree).split(b'\0') if record]
    selected = None
    for record in records:
        metadata, separator, recorded_path = record.partition(b'\t')
        if not separator or recorded_path.decode('utf-8', 'replace') != path:
            continue
        fields = metadata.split()
        if len(fields) == 3:
            selected = fields
            break
    if selected is None:
        _raise(SKILL_MANIFEST_PATH_MISSING)

    mode, object_type, object_id = selected
    if mode == b'120000' or object_type != b'blob':
        _raise(SKILL_MANIFEST_NOT_FILE)
    if not _valid_object_id(object_id):
        _raise(SKILL_MANIFEST_PATH_MISSING)

    size_result = _run_git(
        git_runner, repo_root,
        ['cat-file', '-s', object_id.decode('ascii')],
    )
    if size_result is None or getattr(size_result, 'returncode', 1) != 0:
        _raise(SKILL_MANIFEST_PATH_MISSING)
    try:
        size = int(_stdout(size_result).strip())
    except (TypeError, ValueError):
        _raise(SKILL_MANIFEST_PATH_MISSING)
    if size < 0:
        _raise(SKILL_MANIFEST_PATH_MISSING)
    if size > MAX_SKILL_FILE_SIZE:
        _raise(SKILL_MANIFEST_TOO_LARGE)

    blob = _run_git(
        git_runner, repo_root,
        ['cat-file', 'blob', object_id.decode('ascii')],
    )
    if blob is None or getattr(blob, 'returncode', 1) != 0:
        _raise(SKILL_MANIFEST_PATH_MISSING)
    content = _stdout(blob)
    if len(content) != size:
        _raise(SKILL_MANIFEST_PATH_MISSING)
    try:
        content.decode('utf-8')
    except UnicodeDecodeError:
        _raise(SKILL_MANIFEST_NOT_UTF8)
    return content


def build_manifest(repo_root, commit, role, gate, git_runner=subprocess.run,
                   provider='codex'):
    """Build a deterministic manifest using only blobs reachable at ``commit``.

    ``git_runner`` is injectable for focused tests and receives subprocess.run
    arguments. The returned manifest digest covers all other returned fields.
    """
    if type(commit) is not str or _COMMIT_RE.fullmatch(commit) is None:
        _raise(SKILL_MANIFEST_COMMIT_INVALID)
    paths = resolve(role, gate)
    if provider == 'claude':
        paths = tuple(sorted(
            _CLAUDE_BASE_PATHS + paths[len(_BASE_PATHS):]
        ))

    top = _run_git(
        git_runner, repo_root,
        ['rev-parse', '--show-toplevel'],
    )
    if top is None or getattr(top, 'returncode', 1) != 0:
        _raise(SKILL_MANIFEST_COMMIT_INVALID)
    try:
        top_path = os.path.realpath(_stdout(top).decode('utf-8').strip())
        requested_root = os.path.realpath(repo_root)
    except (UnicodeDecodeError, TypeError, ValueError, OSError):
        _raise(SKILL_MANIFEST_COMMIT_INVALID)
    if not top_path or top_path != requested_root:
        _raise(SKILL_MANIFEST_COMMIT_INVALID)

    checked_commit = _run_git(
        git_runner, repo_root,
        ['rev-parse', '--verify', commit + '^{commit}'],
    )
    if (checked_commit is None
            or getattr(checked_commit, 'returncode', 1) != 0
            or _stdout(checked_commit).strip().lower()
            != commit.lower().encode('ascii')):
        _raise(SKILL_MANIFEST_COMMIT_INVALID)

    entries = []
    for path in paths:
        content = _read_blob(git_runner, repo_root, commit, path)
        entries.append({
            'path': path,
            'sha256': hashlib.sha256(content).hexdigest(),
            'size': len(content),
        })

    manifest = {
        'schema_version': 1,
        'commit': commit.lower(),
        'role': role,
        'gate': gate,
        'entries': entries,
    }
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
    ).encode('utf-8')
    manifest['manifest_sha256'] = hashlib.sha256(canonical).hexdigest()
    return manifest


def read_manifest_files(repo_root, manifest, git_runner=subprocess.run):
    """Read manifest file text from the commit named by a built manifest."""
    if type(manifest) is not dict or type(manifest.get('commit')) is not str:
        _raise(SKILL_MANIFEST_COMMIT_INVALID)
    entries = manifest.get('entries')
    if type(entries) is not list:
        _raise(SKILL_MANIFEST_PATH_MISSING)
    result = []
    for entry in entries:
        if (type(entry) is not dict or type(entry.get('path')) is not str or
                type(entry.get('sha256')) is not str):
            _raise(SKILL_MANIFEST_PATH_MISSING)
        content = _read_blob(
            git_runner, repo_root, manifest['commit'], entry['path'],
        )
        if hashlib.sha256(content).hexdigest() != entry['sha256']:
            _raise(SKILL_MANIFEST_PATH_MISSING)
        result.append((entry['path'], content.decode('utf-8')))
    return result
