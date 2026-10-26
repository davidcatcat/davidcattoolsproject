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
import hashlib
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
D6_INTEGRATOR_DISPATCHER_ROOT = Path(
    "/Users/davidsun/git/davidcataiporject/davidcattoolsproject")
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


# A git lock or a stuck filesystem must not hang a read-only lookup forever.
# Commands that change the repository (worktree add/lock) and the environment
# bootstrap (a venv and wheel install) are not timed: they can legitimately
# take minutes, and a kill would leave a lock or a half-built tree behind.
GIT_TIMEOUT_SECONDS = 120


def _run_git(cwd, *args, check=True, timeout=GIT_TIMEOUT_SECONDS):
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
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
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


def _read_registry_record(path):
    """Read one owner-only D6 record without following a registry symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        _stop("D6_REGISTRY_UNAVAILABLE")
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
                before.st_size > 64 * 1024):
            _stop("D6_REGISTRY_INVALID")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            raw = stream.read(64 * 1024 + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size,
                                 item.st_mtime_ns, item.st_ctime_ns)
        if (len(raw) > 64 * 1024 or identity(before) != identity(after) or
                identity(before) != identity(current)):
            _stop("D6_REGISTRY_CHANGED")
    except LifecycleError:
        raise
    except Exception:
        _stop("D6_REGISTRY_INVALID")
    finally:
        if fd >= 0:
            os.close(fd)

    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                _stop("D6_REGISTRY_INVALID")
            value[key] = item
        return value

    try:
        record = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs,
                            parse_constant=lambda _value: _stop("D6_REGISTRY_INVALID"))
    except LifecycleError:
        raise
    except Exception:
        _stop("D6_REGISTRY_INVALID")
    if type(record) is not dict:
        _stop("D6_REGISTRY_INVALID")
    return record


def _trusted_dispatcher_git():
    """Return pinned Git access after proving the caller is the clean Integrator checkout."""
    dispatcher_root = D6_INTEGRATOR_DISPATCHER_ROOT
    lifecycle_rel = "tools/llmtool/scripts/d6_worktree_lifecycle.py"
    git_env = {
        "PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~"), "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
    }

    def git(*args, input_data=None):
        try:
            result = subprocess.run(
                ["/usr/bin/git", "--no-optional-locks", "--no-replace-objects",
                 "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                 "-C", str(dispatcher_root), *args],
                env=git_env, input=input_data, stdin=(None if input_data is not None
                                                     else subprocess.DEVNULL),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=False, timeout=GIT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            _stop("ENV_BOOTSTRAP_UNAVAILABLE")
        if result.returncode or result.stderr:
            _stop("ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
        return result.stdout

    try:
        if (not dispatcher_root.is_absolute() or
                Path(__file__).resolve() != dispatcher_root / lifecycle_rel or
                Path(os.path.realpath(dispatcher_root)) != dispatcher_root or
                git("rev-parse", "--show-toplevel").decode().rstrip("\n") != str(dispatcher_root) or
                git("remote", "get-url", "origin").decode().strip() not in ORIGINS or
                git("status", "--porcelain=v1", "--untracked-files=all")):
            _stop("ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
        pinned_head = git("rev-parse", "HEAD").decode().strip()
        if re.fullmatch(r"[0-9a-f]{40}", pinned_head) is None:
            _stop("ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
        helper_rel = "tools/llmtool/scripts/d6_worktree_env.py"
        helper_source = git("show", pinned_head + ":" + helper_rel)
        lifecycle_source = git("show", pinned_head + ":" + lifecycle_rel)
        for relative, expected in ((helper_rel, helper_source),
                                   (lifecycle_rel, lifecycle_source)):
            source_path = dispatcher_root / relative
            info = source_path.lstat()
            if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or
                    info.st_uid != os.getuid() or source_path.read_bytes() != expected):
                _stop("ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
    except (OSError, UnicodeDecodeError):
        _stop("ENV_BOOTSTRAP_UNAVAILABLE")
    return dispatcher_root, pinned_head, git, helper_source


def _approved_task_source(root, task_id, relative, dispatcher_root, git):
    """Read one Integrator-approved source blob from the shared Git object store."""
    if not TASK_ID_RE.fullmatch(task_id or ""):
        _stop("ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
    common_dir = Path(git("rev-parse", "--git-common-dir").decode().strip())
    if not common_dir.is_absolute():
        common_dir = dispatcher_root / common_dir
    common_dir = common_dir.resolve(strict=True)
    registry_dir = common_dir / "llmtool-worktrees"
    registry_stat = registry_dir.lstat()
    if (not stat.S_ISDIR(registry_stat.st_mode) or
            registry_stat.st_uid != os.getuid() or
            stat.S_IMODE(registry_stat.st_mode) != 0o700):
        _stop("ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
    record = _read_registry_record(registry_dir / (task_id + ".json"))
    approved_sha = record.get("env2_approved_tooling_sha")
    if approved_sha is None:
        return None
    root_realpath = str(root.resolve(strict=True))
    root_stat = Path(root_realpath).lstat()
    identity = record.get("physical_identity")
    task_head = git("-C", root_realpath, "rev-parse", "HEAD").decode().strip()
    task_branch = git("-C", root_realpath, "branch", "--show-current").decode().strip()
    if (type(approved_sha) is not str or
            not re.fullmatch(r"[0-9a-f]{40}", approved_sha) or
            record.get("schema_version") != 1 or record.get("task_id") != task_id or
            record.get("root_realpath") != root_realpath or
            type(identity) is not dict or
            set(identity) != {"device", "inode", "uid", "mode"} or
            any(type(identity[key]) is not int or identity[key] < 0
                for key in ("device", "inode", "uid")) or
            identity.get("mode") != "0o700" or
            (root_stat.st_dev, root_stat.st_ino, root_stat.st_uid) !=
            (identity["device"], identity["inode"], identity["uid"]) or
            root_stat.st_uid != os.getuid() or not stat.S_ISDIR(root_stat.st_mode) or
            stat.S_IMODE(root_stat.st_mode) != 0o700 or
            record.get("git_common_dir") != str(common_dir) or
            record.get("state") not in ("ACTIVE", "REPAIR") or
            record.get("lock_state") != "LOCKED" or
            type(record.get("base_sha")) is not str or
            not re.fullmatch(r"[0-9a-f]{40}", record["base_sha"]) or
            record.get("head_sha") != approved_sha or task_head != approved_sha or
            type(record.get("branch")) is not str or not record["branch"] or
            task_branch != record.get("branch")):
        _stop("ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
    git("-C", root_realpath, "merge-base", "--is-ancestor",
        record["base_sha"], approved_sha)
    listing = git("ls-tree", "-z", approved_sha, "--", relative)
    entries = listing.split(b"\0")
    if entries and entries[-1] == b"":
        entries.pop()
    if len(entries) != 1 or b"\t" not in entries[0]:
        _stop("ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
    metadata, path = entries[0].split(b"\t", 1)
    fields = metadata.split(b" ")
    if (path != relative.encode() or len(fields) != 3 or
            fields[0] != b"100644" or fields[1] != b"blob" or
            re.fullmatch(rb"[0-9a-f]{40}", fields[2]) is None):
        _stop("ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
    source = git("cat-file", "blob", fields[2].decode())
    if git("hash-object", "--stdin", input_data=source).strip() != fields[2]:
        _stop("ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
    return source


def _run_approved_lifecycle(task_id, root, lifecycle_args):
    """Execute the exact Integrator-approved lifecycle blob without opening its task path."""
    try:
        dispatcher_root, _pinned_head, git, _helper_source = _trusted_dispatcher_git()
        lifecycle_rel = "tools/llmtool/scripts/d6_worktree_lifecycle.py"
        source = _approved_task_source(root, task_id, lifecycle_rel, dispatcher_root, git)
        if source is None:
            _stop("ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
        source_text = source.decode("utf-8")
        source_path = str(dispatcher_root / lifecycle_rel)
        code = ("exec(compile(" + repr(source_text) + ", " + repr(source_path) +
                ", 'exec'), {'__name__': '__main__', '__file__': " +
                repr(source_path) + "})")
        env = {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            "HOME": os.path.expanduser("~"), "LC_ALL": "C", "LANG": "C",
            "PIP_CONFIG_FILE": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
        }
        result = subprocess.run(
            [sys.executable, "-B", "-I", "-c", code, *lifecycle_args],
            cwd=str(dispatcher_root), env=env, stdin=subprocess.DEVNULL, check=False)
    except (OSError, UnicodeDecodeError):
        _stop("ENV_BOOTSTRAP_UNAVAILABLE")
    return result.returncode


def _bootstrap(root, task_id):
    dispatcher_root, _pinned_head, git, helper_source = _trusted_dispatcher_git()
    helper_rel = "tools/llmtool/scripts/d6_worktree_env.py"
    try:
        approved_helper = _approved_task_source(
            root, task_id, helper_rel, dispatcher_root, git)
        if approved_helper is not None:
            helper_source = approved_helper
        helper_code = helper_source.decode("utf-8")
        env = {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            "HOME": os.path.expanduser("~"), "LC_ALL": "C", "LANG": "C",
            "PIP_CONFIG_FILE": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
        }
        result = subprocess.run(
            [sys.executable, "-B", "-I", "-c", helper_code,
             "--task-id", task_id, "--worktree-root", str(root)],
            cwd=str(dispatcher_root), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    except (OSError, UnicodeDecodeError):
        _stop("ENV_BOOTSTRAP_UNAVAILABLE")
    if result.returncode:
        _stop("ENV_BOOTSTRAP_REJECTED")


def create_worktree(*, canonical_root, task_id, agent_id, session_ref,
                    lease_ref, branch, base_sha, workspace_root=TASKS_ROOT,
                    github_login=None, native_session_id=None):
    """Create, register, lock and bootstrap one task-assigned D6 worktree.

    ``github_login`` and ``native_session_id`` are optional bindings that
    ``task complete`` needs for a Reviewer (and a natively pinned Executor).
    """
    workspace_root = Path(workspace_root)
    uid = os.getuid()
    # Pure input checks run before any filesystem access, so a bad value is
    # D6_TASK_INPUT_INVALID even where the canonical root does not exist.
    if (not TASK_ID_RE.fullmatch(task_id or "") or
            not AGENT_RE.fullmatch(agent_id or "") or
            not session_ref or len(session_ref) > 256 or
            any(ord(ch) < 0x20 for ch in session_ref) or
            not lease_ref or len(lease_ref) > 256 or
            any(ord(ch) < 0x20 for ch in lease_ref) or
            not re.fullmatch(r"[0-9a-f]{40}", base_sha or "") or
            (github_login is not None and
             not re.fullmatch(r"[A-Za-z0-9-]{1,39}", github_login)) or
            (native_session_id is not None and
             (not native_session_id or len(native_session_id) > 256 or
              any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in native_session_id)))):
        _stop("D6_TASK_INPUT_INVALID")
    canonical_root = Path(canonical_root).resolve(strict=True)
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
    lock_held = True
    root = workspace_root / task_id
    record_path = registry / (task_id + ".json")
    try:
        _safe_dir(workspace_root, mode=0o700, uid=uid)
        if root.exists() or root.is_symlink() or record_path.exists() or record_path.is_symlink():
            _stop("D6_TASK_ALREADY_EXISTS")
        subprocess_result = _run_git(canonical_root, "worktree", "add", "-b", branch,
                                     str(root), base_sha, check=False, timeout=None)
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
            if github_login is not None:
                record["github_login"] = github_login
            if native_session_id is not None:
                record["native_session_id"] = native_session_id
            _atomic_record(record_path, record)
            lock_result = _run_git(canonical_root, "worktree", "lock", str(root),
                                   check=False, timeout=None)
            if lock_result.returncode:
                _stop("D6_WORKTREE_LOCK_FAILED")
            record["state"] = "ACTIVE"
            record["lock_state"] = "LOCKED"
            record["last_verified_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _replace_record(record_path, record)
            active_record = dict(record)
            # Bootstrap updates the D6 record with its ENV-2 pass marker under
            # this same registry lock. Release the global lock while the
            # isolated bootstrap process runs; the durable ACTIVE/LOCKED record
            # and unique task ID prevent duplicate creation meanwhile.
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            lock_fd = None
            lock_held = False
            try:
                _bootstrap(root, task_id)
            except LifecycleError:
                lock_fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
                lock_info = os.fstat(lock_fd)
                if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != uid or
                        stat.S_IMODE(lock_info.st_mode) != 0o600 or
                        lock_info.st_nlink != 1):
                    os.close(lock_fd)
                    lock_fd = None
                    _stop("D6_REGISTRY_LOCK_INVALID")
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                lock_held = True
                current_record = _read_registry_record(record_path)
                if current_record != active_record:
                    _stop("D6_REGISTRY_CHANGED")
                current_record["state"] = "REPAIR"
                current_record["last_verified_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                _replace_record(record_path, current_record)
                raise
            print("D6_WORKTREE_READY task_id=" + task_id + " head=" + head + " root=" + str(root))
            return root
        except BaseException:
            # Creation or bootstrap failures retain the registered tree for repair.
            # No cleanup is attempted because ignored setup evidence may exist.
            raise
    finally:
        if lock_fd is not None:
            if lock_held:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
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


# Retire accepts only records the PM can still act on; REVIEW_PENDING,
# RETAIN_BLOCKED, REMOVED and hand-written states are kept for a human.
RETIRE_STATES = frozenset(("ACTIVE", "REPAIR", "RETIRE_READY", "INTEGRATED"))
CONTENT_REASONS = frozenset((
    "D6_RETIRE_UNKNOWN_LOCAL_STATE", "D6_RETIRE_RETENTION_PATH_INVALID",
    "D6_RETIRE_RETENTION_PATH_SYMLINK", "D6_RETIRE_RETENTION_PATH_TRACKED",
    "D6_RETIRE_RETENTION_PATH_UNAVAILABLE"))
WORKTREE_GIT_LOCKS = ("index.lock", "HEAD.lock", "ORIG_HEAD.lock")
COMMON_GIT_LOCKS = ("packed-refs.lock", "config.lock", "shallow.lock")
LSOF = "/usr/sbin/lsof"
SHA40_RE = re.compile(r"[0-9a-f]{40}\Z")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _task_git(cwd, *args, check=True, timeout=GIT_TIMEOUT_SECONDS):
    """Git for retire: task-tree hooks, fsmonitor and auto-gc never run."""
    return _run_git(cwd, "--no-optional-locks", "--no-replace-objects",
                    "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                    "-c", "gc.auto=0", *args, check=check, timeout=timeout)


def _process_cwds():
    """(pid, cwd) for every visible process, or None when lsof gives no answer."""
    try:
        result = subprocess.run(
            [LSOF, "-n", "-P", "-w", "-d", "cwd", "-Fpn"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    cwds = []
    pid = None
    for line in result.stdout.decode("utf-8", "surrogateescape").splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("n") and pid is not None:
            cwds.append((pid, line[1:]))
    # This process always has a cwd, so an empty answer means lsof failed.
    return cwds or None


def _live_pids(root):
    """PIDs whose cwd is the root or inside it; None if liveness is unknown."""
    cwds = _process_cwds()
    if cwds is None:
        return None
    prefix = str(root) + "/"
    return sorted({pid for pid, cwd in cwds if cwd == str(root) or cwd.startswith(prefix)})


def _retention_entries(paths):
    """Parse registered retention paths with ENV-2 semantics; None if invalid.

    A trailing ``/`` marks a directory; any other entry is one exact file.
    """
    if type(paths) is not list:
        return None
    entries = []
    for item in paths:
        if (type(item) is not str or not item or item.startswith("/") or
                "\\" in item or any(ord(ch) < 32 or ord(ch) == 127 for ch in item)):
            return None
        is_dir = item.endswith("/")
        relative = item[:-1] if is_dir else item
        parts = relative.split("/")
        if not relative or any(part in ("", ".", "..", ".git") for part in parts):
            return None
        entries.append((relative, is_dir))
    if len(entries) != len(set(entries)):
        return None
    return entries


def _retention_target(root, root_dev, relative, is_dir):
    """Return the exact on-disk retention path, None if absent, or a block code.

    Every component is checked with lstat, so no symlink is followed and the
    target cannot leave the root or cross onto another filesystem.
    """
    current = root
    parts = relative.split("/")
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            return "D6_RETIRE_RETENTION_PATH_UNAVAILABLE"
        if stat.S_ISLNK(info.st_mode):
            return "D6_RETIRE_RETENTION_PATH_SYMLINK"
        if info.st_dev != root_dev:
            return "D6_RETIRE_RETENTION_PATH_INVALID"
        last = index == len(parts) - 1
        if not last and not stat.S_ISDIR(info.st_mode):
            return "D6_RETIRE_RETENTION_PATH_INVALID"
        if last and (stat.S_ISDIR(info.st_mode) != is_dir or
                     (not is_dir and not stat.S_ISREG(info.st_mode))):
            return "D6_RETIRE_RETENTION_PATH_INVALID"
    if os.path.realpath(current) != str(current):
        return "D6_RETIRE_RETENTION_PATH_SYMLINK"
    return current


def _untracked(root):
    """Every untracked path, ignored ones included (no exclude rules)."""
    raw = _task_git(root, "ls-files", "--others", "-z").stdout
    return [path for path in raw.split("\0") if path]


def _safe_relative(path):
    parts = path.split("/")
    return (bool(path) and not path.startswith("/") and "\\" not in path and
            not any(ord(ch) < 32 or ord(ch) == 127 for ch in path) and
            not any(part in ("", ".", "..", ".git") for part in parts))


def _tracked_changes(root):
    """Tracked paths whose index or worktree content differs from HEAD."""
    paths = set()
    for cached in ((), ("--cached",)):
        raw = _task_git(root, "diff", "--name-only", "-z", "--no-renames",
                        "--no-ext-diff", *cached, "HEAD", "--").stdout
        paths.update(path for path in raw.split("\0") if path)
    return sorted(paths)


def _uncommitted_file(root, root_dev, relative):
    """One exact untracked file or symlink beneath root, or None.

    Every parent is checked with lstat, so a symlinked directory is never
    traversed; a symlink leaf is unlinked itself, never its target.
    """
    current = root
    parts = relative.split("/")
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = current.lstat()
        except OSError:
            return None
        if info.st_dev != root_dev:
            return None
        if index < len(parts) - 1:
            if not stat.S_ISDIR(info.st_mode):
                return None
        elif not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            return None
    return current


def _worktree_listing(canonical_root, root):
    """The canonical checkout's porcelain entry for exactly this root, or None."""
    raw = _task_git(canonical_root, "worktree", "list", "--porcelain").stdout
    matches = []
    for stanza in raw.split("\n\n"):
        fields = stanza.splitlines()
        if fields and fields[0] == "worktree " + str(root):
            matches.append(fields[1:])
    if len(matches) != 1:
        return None
    values = {}
    for line in matches[0]:
        key, _sep, value = line.partition(" ")
        values[key] = value
    return values


def _identity_reasons(root, record, canonical_root, common_dir, workspace_root):
    """Registry/physical/Git identity gates; an empty list means all match."""
    recorded = record.get("root_realpath")
    if type(recorded) is not str or not recorded.startswith("/"):
        return ["D6_RETIRE_RECORD_INVALID"]
    recorded_real = os.path.realpath(recorded)
    if (recorded_real == str(canonical_root) or
            recorded_real.startswith(str(canonical_root) + "/") or
            str(canonical_root).startswith(recorded_real + "/")):
        return ["D6_RETIRE_CANONICAL_ROOT"]
    if Path(recorded_real).parent != workspace_root or recorded_real != recorded:
        return ["D6_RETIRE_ROOT_OUTSIDE_WORKSPACE"]
    if recorded != str(root):
        return ["D6_RETIRE_ROOT_MISMATCH"]
    try:
        info = root.lstat()
    except OSError:
        return ["D6_RETIRE_ROOT_UNAVAILABLE"]
    identity = record.get("physical_identity")
    if (not stat.S_ISDIR(info.st_mode) or type(identity) is not dict or
            any(type(identity.get(key)) is not int for key in ("device", "inode", "uid")) or
            (info.st_dev, info.st_ino, info.st_uid) !=
            (identity["device"], identity["inode"], identity["uid"]) or
            info.st_uid != os.getuid() or
            identity.get("mode") != oct(stat.S_IMODE(info.st_mode))):
        return ["D6_RETIRE_ROOT_IDENTITY_MISMATCH"]
    if record.get("git_common_dir") != str(common_dir):
        return ["D6_RETIRE_COMMON_DIR_MISMATCH"]
    if (_task_git(root, "rev-parse", "--show-toplevel").stdout.strip() != str(root) or
            _task_git(root, "rev-parse", "--path-format=absolute",
                      "--git-common-dir").stdout.strip() != str(common_dir)):
        return ["D6_RETIRE_COMMON_DIR_MISMATCH"]
    branch = record.get("branch")
    if (type(branch) is not str or not _valid_branch(canonical_root, branch) or
            _task_git(root, "branch", "--show-current").stdout.strip() != branch):
        return ["D6_RETIRE_BRANCH_MISMATCH"]
    return []


def _publication(root, branch, head, squash):
    """Name the rule proving HEAD's commits are published, or None.

    Uses only local remote-tracking refs; the PM fetches origin first.
    """
    def is_ancestor(commit, ref):
        return _task_git(root, "merge-base", "--is-ancestor", commit, ref,
                         check=False).returncode == 0

    def has_ref(ref):
        return _task_git(root, "rev-parse", "--verify", "--quiet",
                         ref + "^{commit}", check=False).returncode == 0

    origin_branch = "refs/remotes/origin/" + branch
    origin_main = "refs/remotes/origin/main"
    if has_ref(origin_branch) and is_ancestor(head, origin_branch):
        return "origin_branch"
    if not has_ref(origin_main):
        return None
    if is_ancestor(head, origin_main):
        return "origin_main"
    if squash is not None:
        pr_head, squash_sha = squash
        parents = _task_git(root, "rev-list", "--parents", "-n", "1", squash_sha,
                            check=False)
        if (pr_head == head and parents.returncode == 0 and
                len(parents.stdout.split()) == 2 and is_ancestor(squash_sha, origin_main)):
            return "squash_evidence"
    return None


def _covered(path, entries):
    return any(path == relative or (is_dir and path.startswith(relative + "/"))
               for relative, is_dir in entries)


def _pycache_file(root, root_dev, relative):
    """One regular ``__pycache__/*.pyc`` file beneath root, or None."""
    parts = relative.split("/")
    if (len(parts) < 2 or parts[-2] != "__pycache__" or not parts[-1].endswith(".pyc") or
            not _safe_relative(relative)):
        return None
    path = _uncommitted_file(root, root_dev, relative)
    if path is None or not stat.S_ISREG(path.lstat().st_mode):
        return None
    return path


def _unknown_digest(paths):
    """sha256 of the sorted unknown paths joined by NUL (the evidence value)."""
    joined = "\0".join(sorted(paths)).encode("utf-8", "surrogateescape")
    return hashlib.sha256(joined).hexdigest()


def _retire_plan(task_id, record, *, canonical_root, common_dir, workspace_root, squash,
                 discard_uncommitted=False, unknown_evidence=None):
    """Evaluate every retire gate for one task without changing anything.

    With ``discard_uncommitted`` (an Owner-approved task), tracked changes and
    untracked non-ignored files become discard candidates instead of blocks.
    ``unknown_evidence`` is the PM's digest of the exact unknown paths seen in
    a dry run; when it matches, that content (and a RETAIN_BLOCKED state) is
    accepted for removal by ``git worktree remove``.
    """
    root = workspace_root / task_id
    plan = {"task_id": task_id, "root": str(root), "reasons": [],
            "content_block": False, "targets": [], "unknown_paths": [],
            "discard_tracked": [], "discard_untracked": [], "pycache": [],
            "accepted_unknown": False}
    if (record.get("schema_version") != 1 or record.get("task_id") != task_id):
        plan["reasons"].append("D6_RETIRE_RECORD_INVALID")
        return plan
    state = record.get("state")
    if state not in RETIRE_STATES and state != "RETAIN_BLOCKED":
        plan["reasons"].append("D6_RETIRE_STATE_NOT_RETIRABLE")
        return plan
    reasons = _identity_reasons(root, record, canonical_root, common_dir, workspace_root)
    if reasons:
        plan["reasons"].extend(reasons)
        return plan
    branch = record["branch"]
    head = _task_git(root, "rev-parse", "HEAD").stdout.strip()
    plan.update(branch=branch, head=head)
    listing = _worktree_listing(canonical_root, root)
    if (listing is None or listing.get("HEAD") != head or
            listing.get("branch") != "refs/heads/" + branch or "prunable" in listing):
        plan["reasons"].append("D6_RETIRE_WORKTREE_NOT_REGISTERED")
        return plan
    plan["locked"] = "locked" in listing

    live = _live_pids(root)
    if live is None:
        plan["reasons"].append("D6_RETIRE_LIVENESS_UNKNOWN")
    elif live:
        plan["reasons"].append("D6_RETIRE_LIVE_PROCESS")
        plan["live_pids"] = live
    git_dir = Path(_task_git(root, "rev-parse", "--path-format=absolute",
                             "--git-dir").stdout.strip())
    if git_dir.parent != common_dir / "worktrees":
        plan["reasons"].append("D6_RETIRE_COMMON_DIR_MISMATCH")
        return plan
    locks = [git_dir / name for name in WORKTREE_GIT_LOCKS]
    locks += [common_dir / name for name in COMMON_GIT_LOCKS]
    locks.append(common_dir / "refs" / "heads" / (branch + ".lock"))
    if any(os.path.lexists(path) for path in locks):
        plan["reasons"].append("D6_RETIRE_GIT_LOCKED")
    plan["publication"] = _publication(root, branch, head, squash)
    if plan["publication"] is None:
        plan["reasons"].append("D6_RETIRE_UNPUBLISHED")
    if _task_git(root, "status", "--porcelain=v1", "-z",
                 "--untracked-files=no").stdout:
        tracked = _tracked_changes(root)
        if discard_uncommitted and tracked and all(map(_safe_relative, tracked)):
            plan["discard_tracked"] = tracked
        else:
            plan["reasons"].append("D6_RETIRE_DIRTY_TRACKED")

    entries = _retention_entries(record.get("retention_paths"))
    if entries is None:
        plan["reasons"].append("D6_RETIRE_RETENTION_PATH_INVALID")
        plan["content_block"] = True
        return plan
    root_dev = root.lstat().st_dev
    for relative, is_dir in entries:
        target = _retention_target(root, root_dev, relative, is_dir)
        if isinstance(target, str):
            plan["reasons"].append(target)
            plan["content_block"] = True
        elif target is not None:
            if _task_git(root, "ls-files", "-z", "--", relative).stdout:
                plan["reasons"].append("D6_RETIRE_RETENTION_PATH_TRACKED")
                plan["content_block"] = True
            else:
                plan["targets"].append((relative, is_dir))
    candidates = set()
    if discard_uncommitted:
        raw = _task_git(root, "ls-files", "--others", "--exclude-standard", "-z").stdout
        candidates = {path for path in raw.split("\0") if path}
    unknown = []
    for path in _untracked(root):
        if _covered(path, entries):
            continue
        if _pycache_file(root, root_dev, path) is not None:
            plan["pycache"].append(path)
        # Ignored files are never "uncommitted changes"; they stay unknown.
        elif (path in candidates and _safe_relative(path) and
                _uncommitted_file(root, root_dev, path) is not None):
            plan["discard_untracked"].append(path)
        else:
            unknown.append(path)
    plan["unknown_digest"] = _unknown_digest(unknown)
    if unknown:
        plan["unknown_paths"] = unknown[:50]
        plan["unknown_path_count"] = len(unknown)
    if unknown_evidence is not None:
        if unknown_evidence == plan["unknown_digest"]:
            plan["accepted_unknown"] = True
        else:
            plan["reasons"].append("D6_RETIRE_EVIDENCE_MISMATCH")
    elif state == "RETAIN_BLOCKED":
        plan["reasons"].append("D6_RETIRE_STATE_RETAIN_BLOCKED")
    if unknown and not plan["accepted_unknown"]:
        plan["reasons"].append("D6_RETIRE_UNKNOWN_LOCAL_STATE")
        plan["content_block"] = True
    return plan


def _execute_retire(plan, record, record_path, canonical_root, common_dir, workspace_root):
    """Discard approved/regenerable files, unlock and remove one gated tree.

    This code deletes only exact files (Owner-approved uncommitted changes and
    regular ``__pycache__/*.pyc``) and rmdirs emptied ``__pycache__``
    directories. Registered retention paths stay for ``git worktree remove``
    (without --force), which deletes ignored content itself.
    """
    root = Path(plan["root"])
    entries = _retention_entries(record.get("retention_paths"))

    def recheck():
        reasons = _identity_reasons(root, record, canonical_root, common_dir,
                                    workspace_root)
        if reasons:
            return reasons
        if _task_git(root, "rev-parse", "HEAD").stdout.strip() != plan["head"]:
            return ["D6_RETIRE_HEAD_CHANGED"]
        live = _live_pids(root)
        if live is None:
            return ["D6_RETIRE_LIVENESS_UNKNOWN"]
        if live:
            return ["D6_RETIRE_LIVE_PROCESS"]
        if _task_git(root, "status", "--porcelain=v1", "-z",
                     "--untracked-files=no").stdout:
            return ["D6_RETIRE_DIRTY_TRACKED"]
        root_dev = root.lstat().st_dev
        for relative, is_dir in entries:
            target = _retention_target(root, root_dev, relative, is_dir)
            if isinstance(target, str):
                return [target]
        unknown = [path for path in _untracked(root) if not _covered(path, entries)]
        if unknown and not (plan["accepted_unknown"] and
                            _unknown_digest(unknown) == plan["unknown_digest"]):
            return ["D6_RETIRE_UNKNOWN_LOCAL_STATE"]
        return []

    discarded_paths = sorted(plan["discard_tracked"] + plan["discard_untracked"])
    unlocked = False
    try:
        if discarded_paths:
            plan["discarded_uncommitted"] = True
        if plan["discard_tracked"] and _task_git(
                root, "--literal-pathspecs", "restore", "--source=HEAD", "--staged",
                "--worktree", "--", *plan["discard_tracked"],
                check=False, timeout=None).returncode:
            _stop("D6_RETIRE_DISCARD_FAILED")
        for relative in plan["discard_untracked"]:
            path = _uncommitted_file(root, root.lstat().st_dev, relative)
            if path is None:
                _stop("D6_RETIRE_DISCARD_PATH_INVALID")
            os.unlink(path)
        for relative in plan["pycache"]:
            path = _pycache_file(root, root.lstat().st_dev, relative)
            if path is None:
                _stop("D6_RETIRE_DISCARD_PATH_INVALID")
            os.unlink(path)
        for directory in sorted({relative.rsplit("/", 1)[0] for relative in plan["pycache"]}):
            try:
                # Plain rmdir: succeeds only for an emptied real directory.
                os.rmdir(root / directory)
            except OSError:
                pass
        reasons = recheck()
        if not reasons and plan["locked"]:
            if _task_git(canonical_root, "worktree", "unlock", str(root),
                         check=False, timeout=None).returncode:
                reasons = ["D6_RETIRE_UNLOCK_FAILED"]
            else:
                unlocked = True
                reasons = recheck()
        if not reasons and _task_git(canonical_root, "worktree", "remove", str(root),
                                     check=False, timeout=None).returncode:
            reasons = ["D6_RETIRE_REMOVE_FAILED"]
    except LifecycleError as exc:
        reasons = [exc.code]
    except OSError:
        reasons = ["D6_RETIRE_DISCARD_FAILED"]
    if reasons:
        relocked = True
        if unlocked and os.path.lexists(root):
            relocked = _task_git(canonical_root, "worktree", "lock", "--reason",
                                 "D6 retire refused", str(root),
                                 check=False, timeout=None).returncode == 0
            if not relocked:
                reasons.append("D6_RETIRE_RELOCK_FAILED")
        # The state stays as it was; only facts that changed are recorded.
        if plan.get("discarded_uncommitted") or not relocked:
            updated = dict(record)
            now = _now()
            updated.update(last_verified_at=now, evidence_refs=list(
                record.get("evidence_refs") or []) + [
                    "Retire failed " + now + ": " + ",".join(reasons)])
            if plan.get("discarded_uncommitted"):
                updated.update(discarded_uncommitted=True,
                               discarded_uncommitted_paths=discarded_paths)
            if not relocked:
                updated["lock_state"] = "UNLOCKED"
            try:
                _replace_record(record_path, updated)
            except LifecycleError as exc:
                reasons.append(exc.code)
        return reasons
    if os.path.lexists(root) or _worktree_listing(canonical_root, root) is not None:
        return ["D6_RETIRE_REMOVE_UNVERIFIED"]
    if _read_registry_record(record_path) != record:
        return ["D6_REGISTRY_CHANGED"]
    now = _now()
    removed = ", ".join(relative + ("/" if is_dir else "")
                        for relative, is_dir in plan["targets"]) or "none"
    updated = dict(record)
    updated.update(
        state="REMOVED", lock_state="UNLOCKED", head_sha=plan["head"],
        last_verified_at=now, removed_at=now,
        disposition=("Removed " + now + " by d6_worktree_lifecycle.py retire; "
                     "publication=" + plan["publication"] + "; final HEAD " +
                     plan["head"] + "; branch kept."),
        evidence_refs=list(record.get("evidence_refs") or []) + [
            "Retire " + now + ": publication=" + plan["publication"] +
            " head=" + plan["head"] + " retention removed by git=" + removed +
            " pycache files=" + str(len(plan["pycache"])) +
            (" accepted unknown digest=" + plan["unknown_digest"]
             if plan["accepted_unknown"] else "")])
    if discarded_paths:
        updated.update(discarded_uncommitted=True,
                       discarded_uncommitted_paths=discarded_paths)
    _replace_record(record_path, updated)
    return []


def _mark_retain_blocked(record_path, record, reasons):
    updated = dict(record)
    now = _now()
    updated.update(state="RETAIN_BLOCKED", last_verified_at=now,
                   evidence_refs=list(record.get("evidence_refs") or []) + [
                       "Retire refused " + now + ": " + ",".join(reasons)])
    _replace_record(record_path, updated)


def retire_worktrees(*, canonical_root, task_ids, execute=False, squash_evidence=None,
                     discard_uncommitted=(), unknown_evidence=None,
                     workspace_root=TASKS_ROOT):
    """Plan (default) or execute gated retirement; print one JSON line per task.

    Returns True only when every task is ready (dry run) or removed.
    """
    workspace_root = Path(workspace_root)
    squash_evidence = squash_evidence or {}
    unknown_evidence = unknown_evidence or {}
    if (not task_ids or len(task_ids) != len(set(task_ids)) or
            any(not TASK_ID_RE.fullmatch(task_id or "") for task_id in task_ids) or
            not set(squash_evidence) <= set(task_ids) or
            not set(unknown_evidence) <= set(task_ids) or
            len(discard_uncommitted) != len(set(discard_uncommitted)) or
            not set(discard_uncommitted) <= set(task_ids)):
        _stop("D6_TASK_INPUT_INVALID")
    if workspace_root != TASKS_ROOT and not os.environ.get("LLMTOOL_D6_TESTING"):
        _stop("D6_WORKSPACE_ROOT_INVALID")
    workspace_root = Path(os.path.realpath(workspace_root))
    canonical_root = Path(canonical_root).resolve(strict=True)
    if _task_git(canonical_root, "rev-parse", "--show-toplevel").stdout.strip() != str(canonical_root):
        _stop("D6_REPOSITORY_INVALID")
    common_dir = Path(_task_git(canonical_root, "rev-parse", "--path-format=absolute",
                                "--git-common-dir").stdout.strip())
    if (not common_dir.is_absolute() or common_dir.name != ".git" or
            Path(os.path.realpath(common_dir)) != common_dir):
        _stop("D6_COMMON_DIR_INVALID")
    if _task_git(canonical_root, "remote", "get-url", "origin").stdout.strip() not in ORIGINS:
        _stop("D6_ORIGIN_INVALID")
    registry = common_dir / "llmtool-worktrees"
    _safe_dir(registry, mode=0o700, uid=os.getuid())

    lock_fd = None
    if execute:
        try:
            lock_fd = os.open(registry / ".lifecycle.lock",
                              os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.fchmod(lock_fd, 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except OSError:
            _stop("D6_REGISTRY_LOCK_UNAVAILABLE")
    all_ok = True
    try:
        for task_id in task_ids:
            record_path = registry / (task_id + ".json")
            result = {"task_id": task_id, "mode": "execute" if execute else "dry-run"}
            try:
                record = _read_registry_record(record_path)
                plan = _retire_plan(task_id, record, canonical_root=canonical_root,
                                    common_dir=common_dir, workspace_root=workspace_root,
                                    squash=squash_evidence.get(task_id),
                                    discard_uncommitted=task_id in discard_uncommitted,
                                    unknown_evidence=unknown_evidence.get(task_id))
            except LifecycleError as exc:
                record, plan = None, {"root": str(workspace_root / task_id),
                                      "reasons": [exc.code], "content_block": False,
                                      "targets": [], "unknown_paths": [],
                                      "discard_tracked": [], "discard_untracked": [],
                                      "pycache": []}
            result.update({key: plan[key] for key in (
                "root", "branch", "head", "publication", "live_pids", "unknown_paths",
                "unknown_path_count", "unknown_digest") if plan.get(key)})
            if plan.get("publication") == "origin_branch":
                result["publication_note"] = "pushed to origin/<branch>; merge not proven"
            if plan.get("accepted_unknown"):
                result["unknown_accepted"] = True
            result["git_removes_retention_paths"] = [relative + ("/" if is_dir else "")
                                       for relative, is_dir in plan["targets"]]
            result["pycache_discard_count"] = len(plan["pycache"])
            uncommitted = sorted(plan["discard_tracked"] + plan["discard_untracked"])
            if uncommitted:
                result["discard_uncommitted_paths"] = uncommitted
            reasons = plan["reasons"]
            # RETAIN_BLOCKED is durable, so it is written only when every other
            # gate passed and unknown or unsafe content is the sole problem.
            retain_blocked = (plan["content_block"] and record is not None and
                              set(reasons) <= CONTENT_REASONS)
            if not reasons and execute:
                reasons = _execute_retire(plan, record, record_path, canonical_root,
                                          common_dir, workspace_root)
            if reasons:
                all_ok = False
                result["reasons"] = reasons
                result["result"] = "RETAIN_BLOCKED" if retain_blocked else "BLOCKED"
                if (retain_blocked and execute and
                        record.get("state") != "RETAIN_BLOCKED"):
                    try:
                        _mark_retain_blocked(record_path, _read_registry_record(record_path),
                                             reasons)
                        result["registry_updated"] = True
                    except LifecycleError as exc:
                        result["reasons"].append(exc.code)
                elif plan.get("discarded_uncommitted"):
                    result["registry_updated"] = True
            else:
                result["result"] = "REMOVED" if execute else "RETIRE_READY"
                result["registry_updated"] = execute
            result.setdefault("registry_updated", False)
            if plan.get("discarded_uncommitted"):
                result["discarded_uncommitted"] = True
            print(json.dumps(result, sort_keys=True))
    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
    return all_ok


def _parse_squash_evidence(values):
    evidence = {}
    for value in values or []:
        task_id, _sep, shas = value.partition("=")
        pr_head, _sep, squash = shas.partition(":")
        if (task_id in evidence or not SHA40_RE.fullmatch(pr_head) or
                not SHA40_RE.fullmatch(squash)):
            _stop("D6_TASK_INPUT_INVALID")
        evidence[task_id] = (pr_head, squash)
    return evidence


def _parse_unknown_evidence(values):
    evidence = {}
    for value in values or []:
        task_id, _sep, digest = value.partition("=")
        if task_id in evidence or not re.fullmatch(r"[0-9a-f]{64}", digest):
            _stop("D6_TASK_INPUT_INVALID")
        evidence[task_id] = digest
    return evidence


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id")
    parser.add_argument("--agent-id")
    parser.add_argument("--session-ref")
    parser.add_argument("--lease-ref")
    parser.add_argument("--branch")
    parser.add_argument("--base-sha")
    parser.add_argument("--github-login")
    parser.add_argument("--native-session-id")
    parser.add_argument("--resume-task-id")
    parser.add_argument("--resume-root")
    parser.add_argument("--execute-approved-lifecycle", action="store_true")
    parser.add_argument("--retire-task-id", action="append",
                        help="plan retirement of this task (repeatable); dry run by default")
    parser.add_argument("--squash-evidence", action="append",
                        metavar="TASK_ID=PR_HEAD_SHA:SQUASH_SHA",
                        help="merged squash PR: final PR head and its commit on origin/main")
    parser.add_argument("--discard-uncommitted", action="append", metavar="TASK_ID",
                        help="Owner-approved: discard this retire task's tracked and "
                             "untracked changes (repeatable); needs the execute flag to act")
    parser.add_argument("--retain-blocked-evidence", action="append",
                        metavar="TASK_ID=SHA256",
                        help="accept exactly the unknown paths whose digest the dry run "
                             "printed (also needed to retire a RETAIN_BLOCKED record)")
    parser.add_argument("--execute-approved-retire", action="store_true",
                        help="actually discard approved files and remove the worktrees")
    parser.add_argument("--repo-root", default=str(D6_INTEGRATOR_DISPATCHER_ROOT))
    args = parser.parse_args(argv)
    try:
        create_values = (args.task_id, args.agent_id, args.session_ref,
                         args.lease_ref, args.branch, args.base_sha)
        create_options = (args.github_login, args.native_session_id)
        if args.retire_task_id is not None:
            if (any(value is not None for value in create_values + create_options) or
                    args.resume_task_id is not None or args.resume_root is not None or
                    args.execute_approved_lifecycle):
                _stop("D6_TASK_INPUT_INVALID")
            return 0 if retire_worktrees(
                canonical_root=args.repo_root, task_ids=args.retire_task_id,
                execute=args.execute_approved_retire,
                discard_uncommitted=args.discard_uncommitted or (),
                unknown_evidence=_parse_unknown_evidence(args.retain_blocked_evidence),
                squash_evidence=_parse_squash_evidence(args.squash_evidence)) else 1
        if (args.execute_approved_retire or args.squash_evidence or
                args.discard_uncommitted or args.retain_blocked_evidence):
            _stop("D6_TASK_INPUT_INVALID")
        if args.execute_approved_lifecycle:
            if (args.resume_task_id is None or args.resume_root is None or
                    any(value is not None for value in create_values + create_options) or
                    args.repo_root != str(D6_INTEGRATOR_DISPATCHER_ROOT)):
                _stop("D6_TASK_INPUT_INVALID")
            return _run_approved_lifecycle(
                args.resume_task_id, Path(args.resume_root), [
                    "--resume-task-id", args.resume_task_id,
                    "--resume-root", args.resume_root,
                    "--repo-root", args.repo_root,
                ])
        if args.resume_task_id is not None:
            if (args.resume_root is None or
                    any(value is not None for value in create_values + create_options)):
                _stop("D6_TASK_INPUT_INVALID")
            resume_worktree(task_id=args.resume_task_id, root=args.resume_root)
            return 0
        if args.resume_root is not None or any(value is None for value in create_values):
            _stop("D6_TASK_INPUT_INVALID")
        create_worktree(
            canonical_root=args.repo_root, task_id=args.task_id,
            agent_id=args.agent_id, session_ref=args.session_ref,
            lease_ref=args.lease_ref, branch=args.branch, base_sha=args.base_sha,
            github_login=args.github_login, native_session_id=args.native_session_id,
        )
        return 0
    except LifecycleError as exc:
        print("STOP: D6 lifecycle rejected (" + exc.code + ")", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
