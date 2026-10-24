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
    parser.add_argument("--execute-approved-lifecycle", action="store_true")
    parser.add_argument("--repo-root", default=str(D6_INTEGRATOR_DISPATCHER_ROOT))
    args = parser.parse_args(argv)
    try:
        create_values = (args.task_id, args.agent_id, args.session_ref,
                         args.lease_ref, args.branch, args.base_sha)
        if args.execute_approved_lifecycle:
            if (args.resume_task_id is None or args.resume_root is None or
                    any(value is not None for value in create_values) or
                    args.repo_root != str(D6_INTEGRATOR_DISPATCHER_ROOT)):
                _stop("D6_TASK_INPUT_INVALID")
            return _run_approved_lifecycle(
                args.resume_task_id, Path(args.resume_root), [
                    "--resume-task-id", args.resume_task_id,
                    "--resume-root", args.resume_root,
                    "--repo-root", args.repo_root,
                ])
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
