#!/usr/bin/env python3
"""Create and prepare one already-authorized D6 Developer worktree.

This is the repository's deterministic D6 create/start handoff. Callers must
have made the task/Worker assignment and obtained a live Worker session first;
this command records those facts but does not grant task or provider authority.
It creates and locks the task worktree, writes the host-owned D6 record, then
requires ENV-2 bootstrap to pass before returning the usable root.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time


TASKS_ROOT = Path(
    "/Users/davidsun/.local/share/llmtool-dev-workspaces/"
    "davidcattoolsproject/tasks"
)
TASK_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,95}\Z")
AGENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
ORIGINS = frozenset((
    "git@github.com:davidcatcat/davidcattoolsproject.git",
    "https://github.com/davidcatcat/davidcattoolsproject.git",
))
RETENTION_PATHS = [
    "tools/llmtool/.venv/",
    "tools/llmtool/.worktree-env.lock",
    "tools/llmtool/.d6-bootstrap-report.json",
    "tools/llmtool/llmtool.egg-info/",
    "tools/llmtool/llmtool/__pycache__/__init__." +
    sys.implementation.cache_tag + ".pyc",
    "tools/llmtool/build/",
]


class LifecycleError(Exception):
    """Stable, sanitized D6 lifecycle rejection."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _stop(code):
    raise LifecycleError(code)


def _run_git(cwd, *args, check=True):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.path.expanduser("~"),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    }
    try:
        result = subprocess.run(
            ["/usr/bin/git", *args], cwd=str(cwd), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=False, text=True,
        )
    except OSError:
        _stop("D6_GIT_UNAVAILABLE")
    if check and result.returncode:
        _stop("D6_GIT_REJECTED")
    return result


def _safe_dir(path, *, mode, uid):
    try:
        info = path.lstat()
    except OSError:
        _stop("D6_PATH_UNAVAILABLE")
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or
            info.st_uid != uid or stat.S_IMODE(info.st_mode) != mode):
        _stop("D6_PATH_INVALID")
    return info


def _valid_branch(canonical_root, branch):
    if (not isinstance(branch, str) or not branch or branch.startswith("-") or
            "@{" in branch):
        return False
    result = _run_git(canonical_root, "check-ref-format", "refs/heads/" + branch,
                      check=False)
    return result.returncode == 0


def _atomic_record(path, record):
    payload = (json.dumps(record, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":")) + "\n").encode("ascii")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".",
                                         suffix=".tmp", dir=str(path.parent))
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # A same-directory hard link publishes the complete inode atomically
        # and fails if a registration already exists (portable no-replace).
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        temporary = None
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        _stop("D6_REGISTRY_WRITE_FAILED")


def _replace_record(path, record):
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or
                info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
            _stop("D6_REGISTRY_INVALID")
        temporary = path.with_name("." + path.name + "." + str(os.getpid()) + ".tmp")
        if temporary.exists() or temporary.is_symlink():
            _stop("D6_REGISTRY_TEMP_EXISTS")
        _atomic_record(temporary, record)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        _stop("D6_REGISTRY_UPDATE_FAILED")


def _bootstrap(root, task_id):
    script = root / "tools" / "llmtool" / "scripts" / "d6_worktree_env.py"
    env = dict(os.environ)
    for name in tuple(env):
        if name.startswith(("MAKE", "PYTHON")) or name in ("GNUMAKEFLAGS", "MFLAGS"):
            env.pop(name, None)
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-I", str(script), "--task-id", task_id],
            cwd=str(root), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    except OSError:
        _stop("ENV_BOOTSTRAP_UNAVAILABLE")
    if result.returncode:
        _stop("ENV_BOOTSTRAP_REJECTED")


def create_worktree(*, canonical_root, task_id, agent_id, session_ref,
                    lease_ref, branch, base_sha, workspace_root=TASKS_ROOT):
    """Create, register, lock and bootstrap one task-assigned D6 worktree."""
    canonical_root = Path(canonical_root).resolve(strict=True)
    workspace_root = Path(workspace_root)
    uid = os.getuid()
    if (not TASK_ID_RE.fullmatch(task_id or "") or
            not AGENT_RE.fullmatch(agent_id or "") or
            not session_ref or len(session_ref) > 256 or
            any(ord(ch) < 0x20 for ch in session_ref) or
            not lease_ref or len(lease_ref) > 256 or
            any(ord(ch) < 0x20 for ch in lease_ref) or
            not re.fullmatch(r"[0-9a-f]{40}", base_sha or "")):
        _stop("D6_TASK_INPUT_INVALID")
    if workspace_root != TASKS_ROOT and not os.environ.get("LLMTOOL_D6_TESTING"):
        _stop("D6_WORKSPACE_ROOT_INVALID")
    if _run_git(canonical_root, "rev-parse", "--show-toplevel").stdout.strip() != str(canonical_root):
        _stop("D6_REPOSITORY_INVALID")
    common_dir = Path(_run_git(canonical_root, "rev-parse", "--path-format=absolute",
                               "--git-common-dir").stdout.strip())
    if (not common_dir.is_absolute() or common_dir.name != ".git" or
            Path(os.path.realpath(common_dir)) != common_dir):
        _stop("D6_COMMON_DIR_INVALID")
    origin = _run_git(canonical_root, "remote", "get-url", "origin").stdout.strip()
    if origin not in ORIGINS:
        _stop("D6_ORIGIN_INVALID")
    if not _valid_branch(canonical_root, branch):
        _stop("D6_TASK_INPUT_INVALID")
    if _run_git(canonical_root, "cat-file", "-e", base_sha + "^{commit}", check=False).returncode:
        _stop("D6_BASE_UNAVAILABLE")
    if _run_git(canonical_root, "show-ref", "--verify", "--quiet",
                "refs/heads/" + branch, check=False).returncode == 0:
        _stop("D6_BRANCH_EXISTS")

    registry = common_dir / "llmtool-worktrees"
    _safe_dir(registry, mode=0o700, uid=uid)
    lock_path = registry / ".lifecycle.lock"
    try:
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except OSError:
        _stop("D6_REGISTRY_LOCK_UNAVAILABLE")
    root = workspace_root / task_id
    record_path = registry / (task_id + ".json")
    try:
        _safe_dir(workspace_root, mode=0o700, uid=uid)
        if root.exists() or root.is_symlink() or record_path.exists() or record_path.is_symlink():
            _stop("D6_TASK_ALREADY_EXISTS")
        subprocess_result = _run_git(canonical_root, "worktree", "add", "-b", branch,
                                     str(root), base_sha, check=False)
        if subprocess_result.returncode:
            _stop("D6_WORKTREE_CREATE_FAILED")
        try:
            os.chmod(root, 0o700)
            root_info = root.lstat()
            if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != uid:
                _stop("D6_ROOT_INVALID")
            head = _run_git(root, "rev-parse", "HEAD").stdout.strip()
            actual_branch = _run_git(root, "branch", "--show-current").stdout.strip()
            if head != base_sha or actual_branch != branch:
                _stop("D6_SOURCE_BINDING_MISMATCH")
            record = {
                "schema_version": 1,
                "task_id": task_id,
                "trusted_agent_id": agent_id,
                "session_ref": session_ref,
                "lease_ref": lease_ref,
                "root_realpath": str(root.resolve(strict=True)),
                "physical_identity": {
                    "device": root_info.st_dev, "inode": root_info.st_ino,
                    "uid": root_info.st_uid, "mode": "0o700",
                },
                "git_common_dir": str(common_dir),
                "branch": branch,
                "base_sha": base_sha,
                "head_sha": head,
                "state": "REPAIR",
                "last_verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "evidence_refs": ["Created by d6_worktree_lifecycle.py; ENV-2 bootstrap required before Worker start"],
                "retention_paths": RETENTION_PATHS,
                "lock_state": "UNLOCKED",
                "disposition": "ACTIVE_TASK",
            }
            _atomic_record(record_path, record)
            lock_result = _run_git(canonical_root, "worktree", "lock", str(root),
                                   check=False)
            if lock_result.returncode:
                _stop("D6_WORKTREE_LOCK_FAILED")
            record["state"] = "ACTIVE"
            record["lock_state"] = "LOCKED"
            record["last_verified_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _replace_record(record_path, record)
            try:
                _bootstrap(root, task_id)
            except LifecycleError:
                record["state"] = "REPAIR"
                record["last_verified_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                _replace_record(record_path, record)
                raise
            print("D6_WORKTREE_READY task_id=" + task_id + " head=" + head + " root=" + str(root))
            return root
        except BaseException:
            # Creation or bootstrap failures retain the registered tree for repair.
            # No cleanup is attempted because ignored setup evidence may exist.
            raise
    finally:
        os.close(lock_fd)


def resume_worktree(*, task_id, root):
    """Revalidate and bootstrap one existing ACTIVE/REPAIR D6 worktree."""
    if not TASK_ID_RE.fullmatch(task_id or ""):
        _stop("D6_TASK_INPUT_INVALID")
    try:
        root = Path(root).resolve(strict=True)
    except (OSError, TypeError):
        _stop("D6_ROOT_INVALID")
    if not root.is_absolute():
        _stop("D6_ROOT_INVALID")
    # ENV-2 validates the owner-only record, ACTIVE/REPAIR state, physical
    # root, clean executable sources, exact HEAD and live worktree lock.
    _bootstrap(root, task_id)
    print("D6_WORKTREE_RESUMED task_id=" + task_id + " root=" + str(root))
    return root


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id")
    parser.add_argument("--agent-id")
    parser.add_argument("--session-ref")
    parser.add_argument("--lease-ref")
    parser.add_argument("--branch")
    parser.add_argument("--base-sha")
    parser.add_argument("--resume-task-id")
    parser.add_argument("--resume-root")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[3]))
    args = parser.parse_args(argv)
    try:
        create_values = (args.task_id, args.agent_id, args.session_ref,
                         args.lease_ref, args.branch, args.base_sha)
        if args.resume_task_id is not None:
            if (args.resume_root is None or any(value is not None for value in create_values)):
                _stop("D6_TASK_INPUT_INVALID")
            resume_worktree(task_id=args.resume_task_id, root=args.resume_root)
            return 0
        if args.resume_root is not None or any(value is None for value in create_values):
            _stop("D6_TASK_INPUT_INVALID")
        create_worktree(
            canonical_root=args.repo_root, task_id=args.task_id,
            agent_id=args.agent_id, session_ref=args.session_ref,
            lease_ref=args.lease_ref, branch=args.branch, base_sha=args.base_sha,
        )
        return 0
    except LifecycleError as exc:
        print("STOP: D6 lifecycle rejected (" + exc.code + ")", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
