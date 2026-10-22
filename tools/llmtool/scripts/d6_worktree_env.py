#!/usr/bin/env python3
"""Validate the current registered, locked D6 worktree, then bootstrap its env.

This is the ENV-2 entrypoint for the existing D6 lifecycle.  It verifies the
owner-only local registration and the live Git worktree identity before
delegating to the existing ``make env-bootstrap`` target.  It does not create
or mutate Git worktrees, registrations, or provider state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys


_TASK_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,95}\Z")
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_REGISTRY_LIMIT = 64 * 1024
_VALID_STATES = frozenset(("ACTIVE",))
_EXPECTED_ORIGINS = frozenset((
    "git@github.com:davidcatcat/davidcattoolsproject.git",
    "https://github.com/davidcatcat/davidcattoolsproject.git",
))


class PreflightError(Exception):
    """A stable, sanitized D6 preflight rejection."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _reject(code: str) -> None:
    raise PreflightError(code)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject("D6_REGISTRY_INVALID")
        result[key] = value
    return result


def _directory(path: Path, *, expected_uid: int | None = None,
               expected_mode: int | None = None) -> os.stat_result:
    """Require a real directory with no symlink components."""
    if not path.is_absolute() or Path(os.path.abspath(path)) != path:
        _reject("D6_PATH_INVALID")
    for component in reversed(path.parents):
        try:
            info = component.lstat()
        except OSError:
            _reject("D6_PATH_UNAVAILABLE")
        if not stat.S_ISDIR(info.st_mode):
            _reject("D6_PATH_INVALID")
    try:
        info = path.lstat()
    except OSError:
        _reject("D6_PATH_UNAVAILABLE")
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        _reject("D6_PATH_INVALID")
    if expected_uid is not None and info.st_uid != expected_uid:
        _reject("D6_PATH_OWNER_MISMATCH")
    if expected_mode is not None and stat.S_IMODE(info.st_mode) != expected_mode:
        _reject("D6_PATH_MODE_MISMATCH")
    return info


def _read_record(common_dir: Path, task_id: str, uid: int) -> dict:
    registry = common_dir / "llmtool-worktrees"
    _directory(registry, expected_uid=uid, expected_mode=0o700)
    path = registry / (task_id + ".json")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        _reject("D6_REGISTRY_UNAVAILABLE")
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != uid or
                stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
                before.st_size > _REGISTRY_LIMIT):
            _reject("D6_REGISTRY_INVALID")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            raw = stream.read(_REGISTRY_LIMIT + 1)
            after_read = os.fstat(stream.fileno())
        try:
            after_path = path.lstat()
        except OSError:
            _reject("D6_REGISTRY_CHANGED")
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                 info.st_mtime_ns, info.st_ctime_ns)
        if (len(raw) > _REGISTRY_LIMIT or identity(before) != identity(after_read) or
                identity(before) != identity(after_path)):
            _reject("D6_REGISTRY_CHANGED")
    finally:
        if fd >= 0:
            os.close(fd)
    try:
        record = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                            parse_constant=lambda _value: _reject("D6_REGISTRY_INVALID"))
    except PreflightError:
        raise
    except Exception:
        _reject("D6_REGISTRY_INVALID")
    if type(record) is not dict:
        _reject("D6_REGISTRY_INVALID")
    return record


def _run_git(root: Path, *args: str) -> str:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.path.expanduser("~"),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    }
    try:
        result = subprocess.run(
            ["/usr/bin/git", "--no-optional-locks", "--no-replace-objects",
             "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
             "-c", "gc.auto=0", "-C", str(root), *args],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _reject("D6_GIT_IDENTITY_UNAVAILABLE")
    if result.returncode != 0 or result.stderr:
        _reject("D6_GIT_IDENTITY_UNAVAILABLE")
    try:
        return result.stdout.decode("utf-8").rstrip("\n")
    except UnicodeDecodeError:
        _reject("D6_GIT_IDENTITY_INVALID")


def _read_tracked_file(root: Path, relative: str) -> bytes:
    path = root / relative
    parent = path.parent
    while parent != root:
        _directory(parent, expected_uid=os.getuid())
        parent = parent.parent
    try:
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or
                before.st_uid != os.getuid() or before.st_size > 8 * 1024 * 1024):
            _reject("D6_BOOTSTRAP_SOURCE_INVALID")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except PreflightError:
        raise
    except OSError:
        _reject("D6_BOOTSTRAP_SOURCE_UNAVAILABLE")
    try:
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            data = stream.read(8 * 1024 * 1024 + 1)
            after_fd = os.fstat(stream.fileno())
        after_path = path.lstat()
    except OSError:
        _reject("D6_BOOTSTRAP_SOURCE_CHANGED")
    finally:
        if fd >= 0:
            os.close(fd)
    def identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if (len(data) > 8 * 1024 * 1024 or identity(before) != identity(after_fd) or
            identity(before) != identity(after_path)):
        _reject("D6_BOOTSTRAP_SOURCE_CHANGED")
    return data


def _validate_bootstrap_sources(root: Path, base_sha: str) -> None:
    for relative in ("tools/llmtool/Makefile",
                     "tools/llmtool/scripts/worktree_env.py",
                     "tools/llmtool/scripts/d6_worktree_env.py"):
        local = _read_tracked_file(root, relative)
        try:
            committed = subprocess.run(
                ["/usr/bin/git", "--no-optional-locks", "--no-replace-objects",
                 "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                 "-C", str(root), "show", base_sha + ":" + relative],
                env={"PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~"),
                     "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
                     "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
                     "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
                     "GIT_TERMINAL_PROMPT": "0"},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=15, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            _reject("D6_BOOTSTRAP_SOURCE_UNAVAILABLE")
        if committed.returncode != 0 or committed.stderr or local != committed.stdout:
            _reject("D6_BOOTSTRAP_SOURCE_MISMATCH")


def _worktree_entry(root: Path, common_dir: Path, head: str, branch: str) -> None:
    raw = _run_git(root, "worktree", "list", "--porcelain")
    matches = []
    for stanza in raw.split("\n\n"):
        fields = stanza.splitlines()
        if not fields or not fields[0].startswith("worktree "):
            continue
        if fields[0][len("worktree "):] == str(root):
            matches.append(fields[1:])
    if len(matches) != 1:
        _reject("D6_WORKTREE_NOT_REGISTERED")
    fields = matches[0]
    values = {line.split(" ", 1)[0]: line.split(" ", 1)[1]
              for line in fields if " " in line}
    flags = {line for line in fields if " " not in line}
    locked = "locked" in values or "locked" in flags
    prunable = "prunable" in values or "prunable" in flags
    if (values.get("HEAD") != head or
            values.get("branch") != "refs/heads/" + branch or
            not locked or prunable):
        _reject("D6_WORKTREE_LOCK_MISMATCH")


def validate_registered_worktree(task_id: str, root: Path | None = None) -> Path:
    """Validate task record and live Git identity before environment setup."""
    if type(task_id) is not str or _TASK_ID.fullmatch(task_id) is None:
        _reject("D6_TASK_ID_INVALID")
    root = Path(os.path.realpath(os.fspath(root or Path.cwd())))
    if not root.is_absolute():
        _reject("D6_ROOT_INVALID")
    root_stat = _directory(root, expected_uid=os.getuid())
    top = Path(_run_git(root, "rev-parse", "--show-toplevel"))
    if top != root:
        _reject("D6_ROOT_MISMATCH")
    common_dir = Path(_run_git(root, "rev-parse", "--path-format=absolute",
                                "--git-common-dir"))
    if (not common_dir.is_absolute() or
            Path(os.path.realpath(common_dir)) != common_dir or
            not common_dir.name == ".git"):
        _reject("D6_COMMON_DIR_INVALID")
    common_stat = _directory(common_dir, expected_uid=os.getuid())
    record = _read_record(common_dir, task_id, os.getuid())
    identity = record.get("physical_identity")
    if (type(record.get("schema_version")) is not int or
            record.get("schema_version") != 1 or record.get("task_id") != task_id or
            record.get("root_realpath") != str(root) or
            record.get("git_common_dir") != str(common_dir) or
            record.get("state") not in _VALID_STATES or
            record.get("lock_state") != "LOCKED" or
            type(record.get("trusted_agent_id")) is not str or
            not record["trusted_agent_id"] or
            type(record.get("session_ref")) is not str or not record["session_ref"] or
            type(record.get("branch")) is not str or not record["branch"] or
            type(record.get("base_sha")) is not str or
            _SHA40.fullmatch(record["base_sha"]) is None or
            type(record.get("head_sha")) is not str or
            _SHA40.fullmatch(record["head_sha"]) is None or
            type(identity) is not dict or
            any(type(identity.get(key)) is not int or identity[key] < 0
                for key in ("device", "inode", "uid")) or
            type(identity.get("mode")) is not str or
            re.fullmatch(r"0o[0-7]{3,4}", identity["mode"]) is None):
        _reject("D6_REGISTRY_INVALID")
    registered_mode = int(identity["mode"], 8)
    if (registered_mode & 0o022 or
            stat.S_IMODE(root_stat.st_mode) != registered_mode):
        _reject("D6_PATH_MODE_MISMATCH")
    if ((root_stat.st_dev, root_stat.st_ino, root_stat.st_uid) !=
            (identity["device"], identity["inode"], identity["uid"]) or
            identity["uid"] != os.getuid()):
        _reject("D6_ROOT_IDENTITY_MISMATCH")
    if common_stat.st_uid != identity["uid"]:
        _reject("D6_COMMON_DIR_OWNER_MISMATCH")

    branch = _run_git(root, "branch", "--show-current")
    head = _run_git(root, "rev-parse", "HEAD")
    origin = _run_git(root, "remote", "get-url", "origin")
    if (branch != record["branch"] or head != record["head_sha"] or
            head != record["base_sha"] or
            origin not in _EXPECTED_ORIGINS):
        _reject("D6_SOURCE_BINDING_MISMATCH")
    _validate_bootstrap_sources(root, record["base_sha"])
    _worktree_entry(root, common_dir, head, branch)
    if _read_record(common_dir, task_id, os.getuid()) != record:
        _reject("D6_REGISTRY_CHANGED")
    # Recheck the record's physical binding after Git inspection to fail closed
    # if the registered directory changed during validation.
    after = root.lstat()
    if (after.st_dev, after.st_ino, after.st_uid) != (
            identity["device"], identity["inode"], identity["uid"]):
        _reject("D6_ROOT_IDENTITY_MISMATCH")
    return root


def _invoke_bootstrap(root: Path, task_id: str) -> int:
    # Revalidate immediately before Make resolves path-based source files.
    # D6's verified single-writer lease is the remaining cooperative race bound.
    root = validate_registered_worktree(task_id, root)
    print("D6_PREFLIGHT_PASS task_id=" + task_id)
    makefile_dir = root / "tools" / "llmtool"
    makefile = makefile_dir / "Makefile"
    env_script = makefile_dir / "scripts" / "worktree_env.py"
    scripts_dir = makefile_dir / "scripts"
    for directory in (root / "tools", makefile_dir, scripts_dir):
        try:
            info = directory.lstat()
        except OSError:
            _reject("ENV_BOOTSTRAP_SOURCE_MISSING")
        if not stat.S_ISDIR(info.st_mode):
            _reject("ENV_BOOTSTRAP_SOURCE_INVALID")
    for source in (makefile, env_script):
        try:
            info = source.lstat()
        except OSError:
            _reject("ENV_BOOTSTRAP_SOURCE_MISSING")
        if not stat.S_ISREG(info.st_mode):
            _reject("ENV_BOOTSTRAP_SOURCE_INVALID")
    try:
        result = _run_make(
            ["/usr/bin/make", "-C", str(makefile_dir), "env-bootstrap",
             "WORKTREE_ENV_ROOT=../..",
             "WORKTREE_ENV_PYTHON=" + shlex.quote(sys.executable)], cwd=root,
        )
    except (OSError, subprocess.SubprocessError):
        _reject("ENV_BOOTSTRAP_UNAVAILABLE")
    return result.returncode


def _run_make(argv, *, cwd):
    return subprocess.run(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, check=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True,
                        help="registered D6 task whose current worktree is being prepared")
    args = parser.parse_args(argv)
    try:
        root = validate_registered_worktree(args.task_id)
        expected_script = root / "tools" / "llmtool" / "scripts" / "d6_worktree_env.py"
        if Path(os.path.realpath(__file__)) != expected_script:
            _reject("D6_ENTRYPOINT_PATH_MISMATCH")
        return _invoke_bootstrap(root, args.task_id)
    except PreflightError as exc:
        print("STOP: D6 environment preflight rejected (" + exc.code + ")",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
