"""D6 registration and artifact verification for task_completion (#2281).

The code is moved verbatim. Parent helpers are resolved lazily through
task_completion so patching that module path keeps its effect.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from .errors import LLMToolError
from .test_output_excerpts import select_test_excerpts
from .task_completion import (    BOOTSTRAP_SOURCES,
    ENV2_PASS_MARKER_SCHEMA1_KEYS,
    ENV2_PASS_MARKER_SCHEMA2_KEYS,
    REQUIRED_BOOTSTRAP_SOURCES,
    _GIT_LIMIT,
    _HEX40,
    _HEX64,
    _PROPOSAL_FILE_LIMIT,
    _PROPOSAL_TREE_LIMIT,
    _REGISTRY_LIMIT,
    _TASK_ID,
    _TEST_REGISTRY,
    _TIMEOUT,
)


def _stop(*args, **kwargs):
    from . import task_completion as _m
    return _m._stop(*args, **kwargs)


def _unique_pairs(*args, **kwargs):
    from . import task_completion as _m
    return _m._unique_pairs(*args, **kwargs)


def _git(*args, **kwargs):
    from . import task_completion as _m
    return _m._git(*args, **kwargs)


def _is_ancestor(*args, **kwargs):
    from . import task_completion as _m
    return _m._is_ancestor(*args, **kwargs)


def _tree_blob(*args, **kwargs):
    from . import task_completion as _m
    return _m._tree_blob(*args, **kwargs)


def _tree_entry(*args, **kwargs):
    from . import task_completion as _m
    return _m._tree_entry(*args, **kwargs)


def _proposal_blob_size(*args, **kwargs):
    from . import task_completion as _m
    return _m._proposal_blob_size(*args, **kwargs)


@dataclass(frozen=True)
class D6Registration:
    task_id: str
    trusted_agent_id: str
    session_ref: str
    root: str
    common_dir: str
    branch: str
    source_sha: str
    result_sha: str
    state: str
    lock_state: str
    device: int
    inode: int
    uid: int
    native_session_id: str | None
    github_login: str | None
    env2_pass_marker: object
    env2_approved_tooling_sha: str | None


def read_d6_registration(task_id, common_dir, *, allowed_states=('ACTIVE',)):
    """Read one bounded owner-only D6 record without following links."""
    if (type(task_id) is not str or _TASK_ID.fullmatch(task_id) is None or
            type(common_dir) is not str or not os.path.isabs(common_dir) or
            os.path.realpath(common_dir) != common_dir or not common_dir.endswith('/.git')):
        _stop('D6_REGISTRY_ID_INVALID')
    path = Path(common_dir) / 'llmtool-worktrees' / (task_id + '.json')
    try:
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or
                stat.S_IMODE(parent.st_mode) != 0o700):
            _stop('D6_REGISTRY_UNAVAILABLE')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
                before.st_size > _REGISTRY_LIMIT):
            os.close(fd)
            _stop('D6_REGISTRY_INVALID')
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(_REGISTRY_LIMIT + 1)
            again = os.fstat(stream.fileno())
        after = path.lstat()
        if (len(raw) > _REGISTRY_LIMIT or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (again.st_dev, again.st_ino, again.st_size, again.st_mtime_ns) or
                (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            _stop('D6_REGISTRY_CHANGED')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _: _stop('D6_REGISTRY_INVALID'))
    except LLMToolError:
        raise
    except Exception:
        _stop('D6_REGISTRY_UNAVAILABLE')
    if (type(value) is not dict or value.get('schema_version') != 1 or
            value.get('task_id') != task_id or value.get('state') not in allowed_states or
            value.get('lock_state') != 'LOCKED' or
            value.get('git_common_dir') != common_dir or
            type(value.get('trusted_agent_id')) is not str or not value['trusted_agent_id'] or
            type(value.get('session_ref')) is not str or not value['session_ref'] or
            type(value.get('root_realpath')) is not str or
            not os.path.isabs(value['root_realpath']) or
            os.path.realpath(value['root_realpath']) != value['root_realpath'] or
            type(value.get('branch')) is not str or not value['branch'] or
            type(value.get('base_sha')) is not str or _HEX40.fullmatch(value['base_sha']) is None or
            type(value.get('head_sha')) is not str or _HEX40.fullmatch(value['head_sha']) is None):
        _stop('D6_REGISTRY_INVALID')
    identity = value.get('physical_identity')
    identity_keys = set(identity) if type(identity) is dict else None
    legacy_identity = identity_keys == {'device', 'inode', 'uid'}
    lifecycle_identity = identity_keys == {'device', 'inode', 'uid', 'mode'}
    if (not (legacy_identity or lifecycle_identity) or
            (lifecycle_identity and
             (type(identity['mode']) is not str or identity['mode'] != '0o700')) or
            any(type(identity[k]) is not int or identity[k] < 0
                for k in ('device', 'inode', 'uid'))):
        _stop('D6_REGISTRY_INVALID')
    try:
        root_stat = Path(value['root_realpath']).lstat()
    except OSError:
        _stop('D6_ROOT_MISSING')
    if ((root_stat.st_dev, root_stat.st_ino, root_stat.st_uid) !=
            (identity['device'], identity['inode'], identity['uid']) or
            identity['uid'] != os.getuid() or
            stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode) or
            stat.S_IMODE(root_stat.st_mode) != 0o700):
        _stop('D6_ROOT_IDENTITY_MISMATCH')
    native_session = value.get('native_session_id')
    if native_session is not None and (type(native_session) is not str or
            not native_session or len(native_session) > 256 or
            any(ord(c) < 0x20 or ord(c) == 0x7f for c in native_session)):
        _stop('D6_REGISTRY_INVALID')
    github_login = value.get('github_login')
    if github_login is not None and (type(github_login) is not str or
            not re.fullmatch(r'[A-Za-z0-9-]{1,39}', github_login)):
        _stop('D6_REGISTRY_INVALID')
    approved_tooling_sha = value.get('env2_approved_tooling_sha')
    if approved_tooling_sha is not None and (
            type(approved_tooling_sha) is not str or
            _HEX40.fullmatch(approved_tooling_sha) is None or
            approved_tooling_sha != value['head_sha']):
        _stop('D6_REGISTRY_INVALID')
    return D6Registration(task_id, value['trusted_agent_id'], value['session_ref'],
                          value['root_realpath'], common_dir, value['branch'],
                          value['base_sha'], value['head_sha'], value['state'],
                          value['lock_state'], identity['device'], identity['inode'],
                          identity['uid'], native_session, github_login,
                          value.get('env2_pass_marker'), approved_tooling_sha)


def validate_env2_pass_marker(registration, *, expected_root, current_head, write_paths=None):
    """Require the owner-only registry marker and report for this exact D6 HEAD."""
    marker = registration.env2_pass_marker
    if marker is None:
        _stop('D6_ENV2_PASS_MARKER_MISSING')
    fingerprint_re = re.compile(
        r'[a-z][a-z0-9]*\d+\.\d+-[a-z0-9][a-z0-9._-]{0,31}-'
        r'[a-z0-9][a-z0-9._-]{0,31}-[0-9a-f]{12}\Z')
    if type(marker) is not dict:
        _stop('D6_ENV2_PASS_MARKER_INVALID')
    marker_keys = set(marker)
    if marker_keys == ENV2_PASS_MARKER_SCHEMA1_KEYS:
        expected_version = 1
    elif marker_keys == ENV2_PASS_MARKER_SCHEMA2_KEYS:
        expected_version = 2
    else:
        _stop('D6_ENV2_PASS_MARKER_INVALID')
    if (type(marker['schema_version']) is not int or
            marker['schema_version'] != expected_version or
            marker['result'] != 'PASS' or marker['task_id'] != registration.task_id or
            marker['root_realpath'] != expected_root or expected_root != registration.root or
            type(current_head) is not str or _HEX40.fullmatch(current_head) is None or
            marker['submitted_head_sha'] != current_head or
            marker['current_head_sha'] != current_head or
            current_head != registration.result_sha or
            marker['tooling_sha'] != (registration.env2_approved_tooling_sha or
                                      registration.source_sha) or
            type(marker['tooling_sha']) is not str or
            _HEX40.fullmatch(marker['tooling_sha']) is None or
            type(marker['environment_fingerprint']) is not str or
            fingerprint_re.fullmatch(marker['environment_fingerprint']) is None or
            type(marker['report_sha256']) is not str or
            _HEX64.fullmatch(marker['report_sha256']) is None):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    report_path = Path(expected_root) / 'tools' / 'llmtool' / '.d6-bootstrap-report.json'
    for directory in (Path(expected_root) / 'tools', Path(expected_root) / 'tools' / 'llmtool'):
        try:
            info = directory.lstat()
        except OSError:
            _stop('D6_ENV2_PASS_MARKER_MISMATCH')
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    try:
        before = report_path.lstat()
        if (not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or
                before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600 or
                before.st_nlink != 1 or before.st_size > 2 * 1024 * 1024):
            _stop('D6_ENV2_PASS_MARKER_MISMATCH')
        fd = os.open(report_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(fd)
            raw = bytearray()
            while len(raw) <= 2 * 1024 * 1024:
                chunk = os.read(fd, min(65536, 2 * 1024 * 1024 + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(fd)
            current = report_path.lstat()
        finally:
            os.close(fd)
    except LLMToolError:
        raise
    except Exception:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if (len(raw) > 2 * 1024 * 1024 or identity(before) != identity(opened) or
            identity(opened) != identity(after) or
            (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino) or
            hashlib.sha256(raw).hexdigest() != marker['report_sha256']):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    try:
        report = json.loads(bytes(raw).decode('utf-8'), object_pairs_hook=_unique_pairs,
                            parse_constant=lambda _: _stop('D6_ENV2_PASS_MARKER_INVALID'))
    except LLMToolError:
        raise
    except Exception:
        _stop('D6_ENV2_PASS_MARKER_INVALID')
    if (type(report) is not dict or report.get('result') != 'PASS' or
            report.get('task_id') != registration.task_id or
            report.get('root_realpath') != registration.root or
            report.get('head_sha') != current_head or
            report.get('tooling_sha') != marker['tooling_sha'] or
            report.get('cache_fingerprint') != marker['environment_fingerprint']):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    if expected_version == 1:
        return marker

    if (marker.get('environment_mode') != 'CARRIED' or
            type(write_paths) is not list or
            any(type(p) is not str for p in write_paths)):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    if type(report.get('schema_version')) is not int or report.get('schema_version') != 2:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    if (registration.env2_approved_tooling_sha is not None or
            marker['tooling_sha'] != registration.source_sha):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    carried_from = marker.get('carried_from_head_sha')
    if (type(carried_from) is not str or _HEX40.fullmatch(carried_from) is None or
            carried_from == current_head):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    try:
        is_source_ancestor = (registration.source_sha == carried_from or
                              _is_ancestor(expected_root, registration.source_sha, carried_from))
        is_carried_ancestor = _is_ancestor(expected_root, carried_from, current_head)
    except LLMToolError:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    if not is_source_ancestor or not is_carried_ancestor:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    allowed = marker.get('allowed_bootstrap_paths')
    if (type(allowed) is not list or not allowed or
            any(type(p) is not str for p in allowed) or
            allowed != sorted(set(allowed)) or
            not set(allowed).issubset(BOOTSTRAP_SOURCES) or
            not set(allowed).issubset(set(write_paths))):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    try:
        diff_raw = _git(expected_root, ('diff', '--name-only', '-z', '--no-ext-diff',
                                        '--no-textconv', '--no-renames', marker['tooling_sha'],
                                        current_head, '--', *sorted(BOOTSTRAP_SOURCES)),
                        limit=1024 * 1024)
        diff_paths = sorted(set(p for p in diff_raw.decode('utf-8', 'strict').split('\x00') if p))
    except (LLMToolError, UnicodeError):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    if allowed != diff_paths:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    try:
        for req_path in sorted(REQUIRED_BOOTSTRAP_SOURCES):
            if _tree_entry(expected_root, current_head, req_path) is None:
                _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    except LLMToolError:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    if (report.get('status') != 'env_carried' or
            report.get('operations') != 'NONE' or
            report.get('carried_from_head_sha') != carried_from or
            report.get('allowed_bootstrap_paths') != allowed):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    venv_identity = report.get('venv_identity')
    expected_venv_keys = {
        'schema_version', 'path', 'device', 'inode', 'uid',
        'pyvenv_cfg_sha256', 'interpreter_realpath', 'interpreter_stat',
        'site_packages_sha256',
    }
    if type(venv_identity) is not dict or set(venv_identity) != expected_venv_keys:
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')
    if (type(venv_identity['schema_version']) is not int or
            venv_identity['schema_version'] != 1 or
            venv_identity['path'] != 'tools/llmtool/.venv' or
            type(venv_identity['device']) is not int or isinstance(venv_identity['device'], bool) or
            type(venv_identity['inode']) is not int or isinstance(venv_identity['inode'], bool) or
            type(venv_identity['uid']) is not int or isinstance(venv_identity['uid'], bool) or
            type(venv_identity['pyvenv_cfg_sha256']) is not str or
            _HEX64.fullmatch(venv_identity['pyvenv_cfg_sha256']) is None or
            type(venv_identity['interpreter_realpath']) is not str or
            not venv_identity['interpreter_realpath'] or
            type(venv_identity['site_packages_sha256']) is not str or
            _HEX64.fullmatch(venv_identity['site_packages_sha256']) is None):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    stat_obj = venv_identity['interpreter_stat']
    expected_stat_keys = {'device', 'inode', 'size', 'mtime_ns'}
    if (type(stat_obj) is not dict or set(stat_obj) != expected_stat_keys or
            any(type(stat_obj[k]) is not int or isinstance(stat_obj[k], bool)
                for k in expected_stat_keys)):
        _stop('D6_ENV2_PASS_MARKER_MISMATCH')

    return marker


def _tree_entry(root, tree_sha, path):
    raw = _git(root, ('ls-tree', '-z', tree_sha, '--', path), limit=4096)
    if not raw:
        return None
    if not raw.endswith(b'\0') or raw.count(b'\0') != 1:
        _stop('DEVELOPER_ARTIFACT_CHECK_UNKNOWN')
    metadata, entry_path = raw[:-1].split(b'\t', 1)
    fields = metadata.split(b' ')
    if (len(fields) != 3 or entry_path != path.encode('utf-8') or
            fields[1] != b'blob' or fields[0] not in (b'100644', b'100755') or
            re.fullmatch(rb'[0-9a-f]{40}', fields[2]) is None):
        _stop('DEVELOPER_ARTIFACT_TREE_ENTRY_UNSUPPORTED')
    return fields[0].decode('ascii'), fields[2].decode('ascii')


def _tree_blob(root, tree_sha, path):
    entry = _tree_entry(root, tree_sha, path)
    if entry is None:
        return None
    mode, blob_sha = entry
    return mode, _git(root, ('cat-file', 'blob', blob_sha))


def _proposal_blob_size(root, tree_sha, path):
    entry = _tree_entry(root, tree_sha, path)
    if entry is None:
        return 0
    raw_size = _git(root, ('cat-file', '-s', entry[1]), limit=32).strip()
    if not re.fullmatch(rb'(0|[1-9][0-9]{0,12})', raw_size):
        _stop('DEVELOPER_ARTIFACT_CHECK_UNKNOWN')
    size = int(raw_size)
    if size > _GIT_LIMIT:
        _stop('DEVELOPER_ARTIFACT_BLOB_TOO_LARGE')
    return size


def _select_test_excerpts(*args, **kwargs):
    from . import task_completion as _m
    return _m.select_test_excerpts(*args, **kwargs)


def _test_excerpt(home, output, code):
    """Return only bounded excerpts accepted by the sensitive-output screener."""
    if not code:
        return None
    # macOS may expose temporary directories through a /var symlink. Resolve
    # the trusted TemporaryDirectory path before the excerpt reader's strict
    # no-follow walk, which intentionally rejects symlinked components.
    capture = Path(home).resolve(strict=True) / 'test-output.log'
    capture.write_bytes(output)
    try:
        selected = _select_test_excerpts(str(capture), code)
    except LLMToolError:
        return None
    return selected['excerpts']






