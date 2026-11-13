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
import calendar
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time


# Legacy-only constants (#369): no lifecycle decision reads these any more;
# trust.json is authoritative. They stay defined, with their pre-PR-3 values,
# until PR-10 so `llmtool trust verify-legacy` can compare against them.
TASKS_ROOT = Path(
    "/Users/davidsun/.local/share/llmtool-dev-workspaces/"
    "davidcattoolsproject/tasks"
)
ORIGINS = frozenset((
    "git@github.com:davidcatcat/davidcattoolsproject.git",
    "https://github.com/davidcatcat/davidcattoolsproject.git",
))
D6_INTEGRATOR_DISPATCHER_ROOT = Path(
    "/Users/davidsun/git/davidcataiporject/davidcattoolsproject")
TASK_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,95}\Z")
AGENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
RETENTION_PATHS = [
    "tools/llmtool/.venv/",
    "tools/llmtool/.worktree-env.lock",
    "tools/llmtool/.d6-bootstrap-report.json",
    "tools/llmtool/llmtool.egg-info/",
    "tools/llmtool/llmtool/__pycache__/__init__." +
    sys.implementation.cache_tag + ".pyc",
    "tools/llmtool/build/",
]
# Writer lease (#1019 A11 row 43): a registration's lease runs this long from
# the writer's last recorded activity (creation or a recorded result). An
# expired lease is only reported; a PM releases it explicitly.
LEASE_SECONDS = 24 * 60 * 60
LEASE_TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
LEASE_RELEASED_STATE = "LEASE_RELEASED"
LEASE_RELEASE_STATES = frozenset(("ACTIVE", "REPAIR"))


class LifecycleError(Exception):
    """Stable, sanitized D6 lifecycle rejection."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _stop(code):
    raise LifecycleError(code)


class TrustError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _fail(code):
    raise TrustError(code)


MAX_TRUST_BYTES = 16 * 1024
_HAS_DIR_FD_SUPPORT = (os.name == "posix" and hasattr(os, "O_DIRECTORY") and
                       hasattr(os, "O_NOFOLLOW"))


# BEGIN llmtool-trust-reader v1
def _check_directory(fd, uid, code, private=False):
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        _fail(code)
    if private:
        if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
            _fail("TRUST_DIRECTORY_NOT_PRIVATE")
    elif (info.st_uid not in (0, uid) or
          (stat.S_IMODE(info.st_mode) & 0o022 and not
           (info.st_uid == 0 and (info.st_mode & stat.S_ISVTX)))):
        _fail(code)
    return info


def _read_trust_bytes(home, uid):
    if not _HAS_DIR_FD_SUPPORT:
        _fail("TRUST_UNSUPPORTED_PLATFORM")
    flags_dir = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    flags_file = (os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) |
                  getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open("/", flags_dir)
    except OSError:
        _fail("TRUST_ANCESTOR_INVALID")
    open_fds = [fd]
    try:
        _check_directory(fd, uid, "TRUST_ANCESTOR_INVALID")
        components = [part for part in home.strip("/").split("/") if part] + [".config", "llmtool"]
        current = ""
        for index, part in enumerate(components):
            current += "/" + part
            try:
                child = os.open(part, flags_dir, dir_fd=fd)
            except FileNotFoundError:
                _fail("TRUST_MISSING")
            except OSError:
                _fail("TRUST_ANCESTOR_INVALID")
            open_fds.append(child)
            os.close(fd)
            open_fds.remove(fd)
            fd = child
            is_private = index == len(components) - 1
            _check_directory(fd, uid, "TRUST_ANCESTOR_INVALID", is_private)
            try:
                os.stat(".git", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except OSError:
                _fail("TRUST_ANCESTOR_INVALID")
            else:
                _fail("TRUST_INSIDE_WORKTREE")

        try:
            file_fd = os.open("trust.json", flags_file, dir_fd=fd)
        except FileNotFoundError:
            _fail("TRUST_MISSING")
        except OSError:
            _fail("TRUST_FILE_NOT_PRIVATE")
        open_fds.append(file_fd)
        before = os.fstat(file_fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != uid or
                stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1):
            _fail("TRUST_FILE_NOT_PRIVATE")
        if before.st_size < 1 or before.st_size > MAX_TRUST_BYTES:
            _fail("TRUST_TOO_LARGE")
        raw = os.read(file_fd, MAX_TRUST_BYTES + 1)
        after = os.fstat(file_fd)
        try:
            path_info = os.stat("trust.json", dir_fd=fd, follow_symlinks=False)
        except OSError:
            _fail("TRUST_CHANGED_DURING_READ")
        identity = lambda info: (info.st_dev, info.st_ino, info.st_uid, info.st_mode,
                                 info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(after) or identity(before) != identity(path_info):
            _fail("TRUST_CHANGED_DURING_READ")
        return raw
    except TrustError:
        raise
    except (TypeError, NotImplementedError):
        # POSIX platforms without dir_fd support must fail closed with a
        # sanitized platform code, even if their function identities differ.
        _fail("TRUST_UNSUPPORTED_PLATFORM")
    except OSError:
        _fail("TRUST_ANCESTOR_INVALID")
    finally:
        for item in reversed(open_fds):
            try:
                os.close(item)
            except OSError:
                pass
# END llmtool-trust-reader v1


def _load_trust(home=None):
    if os.name != "posix" or sys.platform.startswith("win"):
        _fail("TRUST_UNSUPPORTED_PLATFORM")
    if os.getuid() != os.geteuid() or os.geteuid() == 0:
        _fail("TRUST_IDENTITY_INVALID")
    if home is None:
        try:
            home = pwd.getpwuid(os.getuid()).pw_dir
        except (KeyError, OSError):
            _fail("TRUST_HOME_INVALID")
    if (not os.path.isabs(home) or os.path.normpath(home) != home or
            os.path.realpath(home) != home):
        _fail("TRUST_HOME_INVALID")
    raw = _read_trust_bytes(home, os.getuid())
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("TRUST_MALFORMED")
            result[key] = value
        return result

    try:
        if raw.startswith(b"\xef\xbb\xbf"):
            _fail("TRUST_MALFORMED")
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique_pairs,
            parse_constant=lambda _value: _fail("TRUST_MALFORMED"))
        roots = (value["canonical_root"], value["workspace_root"], value["tasks_root"])
        repository = value["repository"]
        slug = repository["slug"]
        origins = repository["allowed_origins"]
        allowed = {"git@github.com:" + slug + ".git",
                   "https://github.com/" + slug + ".git"}
        if (type(value) is not dict or
                set(value) != {"schema_version", "canonical_root", "workspace_root",
                               "tasks_root", "repository", "ci_trust", "tools"} or
                type(repository) is not dict or
                set(repository) != {"slug", "allowed_origins", "approval_refs"} or
                type(value.get("schema_version")) is not int or value["schema_version"] != 1 or
                any(not isinstance(path, str) or not os.path.isabs(path) for path in roots) or
                not isinstance(origins, list) or not 1 <= len(origins) <= 2 or
                len(origins) != len(set(origins)) or not set(origins) <= allowed):
            _fail("TRUST_SCHEMA_INVALID")
        refs = repository["approval_refs"]
        if type(refs) is not dict:
            _fail("TRUST_SCHEMA_INVALID")
        # Read lazily: only --release-retain needs these issue numbers, and it
        # fails closed (TRUST_SCHEMA_INVALID) when the field is absent.
        retain_issues = refs.get("retain_release_issues")
        if retain_issues is not None and (
                type(retain_issues) is not list or not 1 <= len(retain_issues) <= 8 or
                any(type(item) is not int or not 1 <= item <= 10 ** 9
                    for item in retain_issues) or
                len(retain_issues) != len(set(retain_issues))):
            _fail("TRUST_SCHEMA_INVALID")
        # Same canonical identity JSON as llmtool.trust._binding (#369 section 7.2).
        binding = hashlib.sha256(json.dumps(
            {"canonical_root": roots[0], "workspace_root": roots[1],
             "tasks_root": roots[2], "slug": slug, "allowed_origins": origins},
            sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()
    except TrustError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        _fail("TRUST_MALFORMED")
    return {"canonical_root": roots[0], "workspace_root": roots[1],
            "tasks_root": roots[2], "origins": frozenset(origins),
            "slug": slug,
            "retain_release_issues": (None if retain_issues is None
                                      else tuple(retain_issues)),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "binding_sha256": binding}


def _recheck_trust_after_lock(trust):
    """Re-read trust under the lifecycle lock; a changed file stops the run (7.1)."""
    current = _load_trust()
    if current["sha256"] != trust["sha256"]:
        _fail("TRUST_CHANGED_DURING_RUN")
    return current


def _trust_binding_status(record, trust):
    """Return ``stamped``, ``legacy`` or ``rotated`` for one registry record.

    A stamped record must carry the current ``trust_binding_sha256``; only the
    audit-only ``trust_sha256`` may differ. A legacy record (neither stamp) is
    judged by the current trust path rules and is never stamped here (7.4).
    """
    if "trust_sha256" not in record and "trust_binding_sha256" not in record:
        root = record.get("root_realpath")
        common_dir = record.get("git_common_dir")
        tasks_prefix = trust["tasks_root"].rstrip("/") + "/"
        if (type(root) is str and type(common_dir) is str and
                os.path.normpath(root) == root and root.startswith(tasks_prefix) and
                common_dir == os.path.join(trust["canonical_root"], ".git")):
            return "legacy"
        return "rotated"
    binding = record.get("trust_binding_sha256")
    if type(binding) is str and binding == trust["binding_sha256"]:
        return "stamped"
    return "rotated"


def _rotated_record_root(record, task_id, canonical_root, common_dir):
    """The validated recorded root of a trust-rotated record, or None (7.3).

    A rotation may move ``tasks_root``, so retire, reconcile and
    --release-retain handle a rotated record at the root recorded at creation
    instead of the current workspace. That path must be absolute, normal and
    free of symlinks, be named after the task, sit outside the canonical
    checkout and belong to this repository; its parent (the record's own
    tasks_root) must be a real directory owned by this user when it exists.
    """
    recorded = record.get("root_realpath")
    if (type(recorded) is not str or not os.path.isabs(recorded) or
            os.path.normpath(recorded) != recorded or
            os.path.realpath(recorded) != recorded or
            os.path.basename(recorded) != task_id or
            record.get("git_common_dir") != str(common_dir)):
        return None
    canonical = str(canonical_root)
    if (recorded == canonical or recorded.startswith(canonical + "/") or
            canonical.startswith(recorded + "/")):
        return None
    root = Path(recorded)
    if root.parent == Path(root.anchor):
        return None
    try:
        info = root.parent.lstat()
    except FileNotFoundError:
        return root
    except OSError:
        return None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        return None
    return root


def _require_trust_binding(record, trust):
    status = _trust_binding_status(record, trust)
    if status == "rotated":
        _stop("D6_TRUST_ROTATED")
    return status


def _retain_release_re(trust):
    """Owner approval comment URLs accepted by --release-retain, from trust."""
    issues = trust.get("retain_release_issues")
    if not issues or type(trust.get("slug")) is not str:
        _fail("TRUST_SCHEMA_INVALID")
    return re.compile(
        r"https://github\.com/" + re.escape(trust["slug"]) + r"/issues/(?:" +
        "|".join(str(issue) for issue in issues) + r")#issuecomment-[0-9]+\Z")


# A git lock or a stuck filesystem must not hang a read-only lookup forever.
# Commands that change the repository (worktree add/lock) and the environment
# bootstrap (a venv and wheel install) are not timed: they can legitimately
# take minutes, and a kill would leave a lock or a half-built tree behind.
GIT_TIMEOUT_SECONDS = 120
BOOTSTRAP_STDERR_LIMIT = 4 * 1024
BOOTSTRAP_STOP_RE = re.compile(rb"STOP: .+ \(([A-Z0-9_]{1,64})\)")


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
    trust = _load_trust()
    dispatcher_root = Path(trust["canonical_root"])
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
                git("remote", "get-url", "origin").decode().strip() not in trust["origins"] or
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
        trust = _load_trust()
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
            [sys.executable, "-B", "-I", "-c", code, *lifecycle_args,
             "--trust-sha256", trust["sha256"]],
            cwd=str(dispatcher_root), env=env, stdin=subprocess.DEVNULL, check=False)
    except (OSError, UnicodeDecodeError):
        _stop("ENV_BOOTSTRAP_UNAVAILABLE")
    return result.returncode


def _bootstrap(root, task_id):
    dispatcher_root, _pinned_head, git, helper_source = _trusted_dispatcher_git()
    trust = _load_trust()
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
        stderr_read_fd, stderr_write_fd = os.pipe()
        captured_stderr = bytearray()
        stderr_oversized = []

        def drain_stderr():
            with os.fdopen(stderr_read_fd, "rb") as stream:
                while True:
                    chunk = stream.read(1024)
                    if not chunk:
                        break
                    available = BOOTSTRAP_STDERR_LIMIT + 1 - len(captured_stderr)
                    if available > 0:
                        captured_stderr.extend(chunk[:available])
                    if len(chunk) > available or len(captured_stderr) > BOOTSTRAP_STDERR_LIMIT:
                        stderr_oversized.append(True)

        stderr_reader = threading.Thread(target=drain_stderr)
        stderr_reader.start()
        try:
            result = subprocess.run(
                [sys.executable, "-B", "-I", "-c", helper_code,
                 "--task-id", task_id, "--worktree-root", str(root),
                 "--trust-sha256", trust["sha256"]],
                cwd=str(dispatcher_root), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=stderr_write_fd, check=False,
            )
        finally:
            os.close(stderr_write_fd)
            stderr_reader.join()
    except (OSError, UnicodeDecodeError):
        _stop("ENV_BOOTSTRAP_UNAVAILABLE")
    if result.returncode:
        lines = bytes(captured_stderr).splitlines()
        match = (BOOTSTRAP_STOP_RE.fullmatch(lines[0])
                 if not stderr_oversized and len(lines) == 1 else None)
        code = match.group(1).decode("ascii") if match else None
        _stop("ENV_BOOTSTRAP_REJECTED:" + code if code else "ENV_BOOTSTRAP_REJECTED")


def create_worktree(*, canonical_root, task_id, agent_id, session_ref,
                    lease_ref, branch, base_sha, workspace_root=None,
                    github_login=None, native_session_id=None):
    """Create, register, lock and bootstrap one task-assigned D6 worktree.

    ``github_login`` and ``native_session_id`` are optional bindings that
    ``task complete`` needs for a Reviewer (and a natively pinned Executor).
    """
    trust = _load_trust()
    workspace_root = Path(trust["tasks_root"] if workspace_root is None else workspace_root)
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
    if workspace_root != Path(trust["tasks_root"]) and not os.environ.get("LLMTOOL_D6_TESTING"):
        _stop("D6_WORKSPACE_ROOT_INVALID")
    if _run_git(canonical_root, "rev-parse", "--show-toplevel").stdout.strip() != str(canonical_root):
        _stop("D6_REPOSITORY_INVALID")
    common_dir = Path(_run_git(canonical_root, "rev-parse", "--path-format=absolute",
                               "--git-common-dir").stdout.strip())
    if (not common_dir.is_absolute() or common_dir.name != ".git" or
            Path(os.path.realpath(common_dir)) != common_dir):
        _stop("D6_COMMON_DIR_INVALID")
    origin = _run_git(canonical_root, "remote", "get-url", "origin").stdout.strip()
    if origin not in trust["origins"]:
        _stop("D6_ORIGIN_INVALID")
    if not os.environ.get("LLMTOOL_D6_TESTING") and str(canonical_root) != trust["canonical_root"]:
        _stop("D6_REPOSITORY_INVALID")
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
        trust = _recheck_trust_after_lock(trust)
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
                "lease_expires_at": _lease_expiry(),
                "evidence_refs": ["Created by d6_worktree_lifecycle.py; ENV-2 bootstrap required before Worker start"],
                "retention_paths": RETENTION_PATHS,
                "lock_state": "UNLOCKED",
                "disposition": "ACTIVE_TASK",
                # Registry stamps (#369 section 7.2): the full file digest is
                # audit-only; the binding digest is enforced by later steps.
                "trust_sha256": trust["sha256"],
                "trust_binding_sha256": trust["binding_sha256"],
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
            record["lease_expires_at"] = _lease_expiry()
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
    # A record stamped under another trust binding is never resumed (7.3).
    # A missing record is left to ENV-2, which reports it with its own code.
    trust = _load_trust()
    common_dir = _task_git(Path(trust["canonical_root"]), "rev-parse",
                           "--path-format=absolute", "--git-common-dir").stdout.strip()
    record_path = Path(common_dir) / "llmtool-worktrees" / (task_id + ".json")
    if os.path.lexists(record_path):
        _require_trust_binding(_read_registry_record(record_path), trust)
    # ENV-2 validates the owner-only record, ACTIVE/REPAIR state, physical
    # root, clean executable sources, exact HEAD and live worktree lock.
    _bootstrap(root, task_id)
    print("D6_WORKTREE_RESUMED task_id=" + task_id + " root=" + str(root))
    return root


def _validate_record_result_report_path(root):
    """Keep the ENV-2 reader's no-symlink guarantee for report directories."""
    for directory in (root / "tools", root / "tools" / "llmtool"):
        try:
            info = directory.lstat()
        except OSError:
            _stop("D6_RECORD_RESULT_ENV2_MARKER_INVALID")
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            _stop("D6_RECORD_RESULT_ENV2_MARKER_INVALID")


def record_result(*, task_id, canonical_root, root=None, workspace_root=None):
    """Advance a registered D6 result HEAD after commit, without changing Git."""
    trust = _load_trust()
    workspace_root = Path(trust["tasks_root"] if workspace_root is None else workspace_root)
    if not TASK_ID_RE.fullmatch(task_id or ""):
        _stop("D6_RECORD_RESULT_TASK_INVALID")
    if workspace_root != Path(trust["tasks_root"]) and not os.environ.get("LLMTOOL_D6_TESTING"):
        _stop("D6_RECORD_RESULT_WORKSPACE_INVALID")
    try:
        canonical_root = Path(canonical_root).resolve(strict=True)
        workspace_root = Path(os.path.realpath(workspace_root))
        root = None if root is None else Path(root).resolve(strict=True)
    except (OSError, TypeError):
        _stop("D6_RECORD_RESULT_ROOT_MISMATCH")
    if _task_git(canonical_root, "rev-parse", "--show-toplevel").stdout.strip() != str(canonical_root):
        _stop("D6_RECORD_RESULT_CANONICAL_ROOT")
    common_dir = Path(_task_git(canonical_root, "rev-parse", "--path-format=absolute",
                                "--git-common-dir").stdout.strip())
    if (not common_dir.is_absolute() or common_dir.name != ".git" or
            Path(os.path.realpath(common_dir)) != common_dir):
        _stop("D6_RECORD_RESULT_COMMON_DIR_MISMATCH")
    registry = common_dir / "llmtool-worktrees"
    _safe_dir(registry, mode=0o700, uid=os.getuid())
    try:
        lock_fd = os.open(registry / ".lifecycle.lock",
                          os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        lock_info = os.fstat(lock_fd)
        if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid() or
                stat.S_IMODE(lock_info.st_mode) != 0o600 or lock_info.st_nlink != 1):
            os.close(lock_fd)
            _stop("D6_REGISTRY_LOCK_INVALID")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except LifecycleError:
        raise
    except OSError:
        _stop("D6_REGISTRY_LOCK_UNAVAILABLE")
    try:
        trust = _recheck_trust_after_lock(trust)
        record_path = registry / (task_id + ".json")
        if not os.path.lexists(record_path):
            _stop("D6_RECORD_RESULT_UNKNOWN_TASK")
        record = _read_registry_record(record_path)
        if record.get("task_id") != task_id:
            _stop("D6_RECORD_RESULT_UNKNOWN_TASK")
        if root is None:
            try:
                root = Path(record.get("root_realpath")).resolve(strict=True)
            except (OSError, TypeError):
                _stop("D6_RECORD_RESULT_ROOT_MISMATCH")
        if record.get("state") in ("RETAIN_BLOCKED", "REMOVED"):
            _stop("D6_RECORD_RESULT_STATE_REFUSED")
        if record.get("state") not in ("ACTIVE", "REPAIR") or record.get("lock_state") != "LOCKED":
            _stop("D6_RECORD_RESULT_STATE_REFUSED")
        if record.get("env2_approved_tooling_sha") is not None:
            _stop("D6_RECORD_RESULT_APPROVED_TOOLING_REFUSED")
        if str(root) == str(canonical_root):
            _stop("D6_RECORD_RESULT_CANONICAL_ROOT")
        if str(root) != record.get("root_realpath") or root.parent != workspace_root:
            _stop("D6_RECORD_RESULT_ROOT_MISMATCH")
        try:
            identity = root.lstat()
        except OSError:
            _stop("D6_RECORD_RESULT_ROOT_MISMATCH")
        registered_identity = record.get("physical_identity")
        if (type(registered_identity) is not dict or
                (identity.st_dev, identity.st_ino, identity.st_uid) !=
                (registered_identity.get("device"), registered_identity.get("inode"),
                 registered_identity.get("uid")) or
                identity.st_uid != os.getuid() or not stat.S_ISDIR(identity.st_mode) or
                stat.S_IMODE(identity.st_mode) != 0o700 or
                registered_identity.get("mode") != "0o700"):
            _stop("D6_RECORD_RESULT_ROOT_MISMATCH")
        if record.get("git_common_dir") != str(common_dir):
            _stop("D6_RECORD_RESULT_COMMON_DIR_MISMATCH")
        if (_task_git(root, "rev-parse", "--show-toplevel").stdout.strip() != str(root) or
                _task_git(root, "rev-parse", "--path-format=absolute",
                          "--git-common-dir").stdout.strip() != str(common_dir)):
            _stop("D6_RECORD_RESULT_ROOT_MISMATCH")
        _require_trust_binding(record, trust)
        _validate_record_result_report_path(root)
        branch = record.get("branch")
        if (type(branch) is not str or not _valid_branch(canonical_root, branch) or
                _task_git(root, "branch", "--show-current").stdout.strip() != branch):
            _stop("D6_RECORD_RESULT_BRANCH_MISMATCH")
        status = _task_git(root, "status", "--porcelain=v1", "--untracked-files=all").stdout
        if status:
            _stop("D6_RECORD_RESULT_DIRTY")
        old_head = record.get("head_sha")
        head = _task_git(root, "rev-parse", "--verify", "HEAD").stdout.strip()
        if (type(old_head) is not str or not SHA40_RE.fullmatch(old_head) or
                not SHA40_RE.fullmatch(head)):
            _stop("D6_RECORD_RESULT_REGISTRY_INVALID")
        if _task_git(root, "merge-base", "--is-ancestor", old_head, head,
                     check=False).returncode:
            _stop("D6_RECORD_RESULT_NON_DESCENDANT")
        previous_record = dict(record)
        updated = dict(record)
        updated["head_sha"] = head
        _replace_record(record_path, updated)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        try:
            # ENV-2 rechecks the exact registered HEAD and publishes its own
            # report and PASS marker. It also takes the lifecycle lock itself.
            _bootstrap(root, task_id)
        except LifecycleError:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                current = _read_registry_record(record_path)
                if (current.get("task_id") != task_id or
                        current.get("head_sha") != head):
                    _stop("D6_REGISTRY_CHANGED")
                _replace_record(record_path, previous_record)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            raise
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            current = _read_registry_record(record_path)
            if (current.get("task_id") != task_id or current.get("head_sha") != head or
                    not isinstance(current.get("env2_pass_marker"), dict) or
                    current["env2_pass_marker"].get("current_head_sha") != head):
                _stop("D6_RECORD_RESULT_ENV2_MARKER_INVALID")
            current["last_verified_at"] = _now()
            if "lease_expires_at" in current:
                # Recording a result is writer activity: the lease runs again
                # from now. A legacy record without a lease stays unchanged.
                current["lease_expires_at"] = _lease_expiry()
            _replace_record(record_path, current)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        print("D6_RESULT_RECORDED task_id=" + task_id + " head=" + head)
        return head
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def bind_native_session(*, task_id, native_session_id, canonical_root,
                        workspace_root=None):
    """Bind a native session to a registered D6 task exactly once."""
    trust = _load_trust()
    workspace_root = Path(trust["tasks_root"] if workspace_root is None else workspace_root)
    if (not TASK_ID_RE.fullmatch(task_id or "") or
            not isinstance(native_session_id, str) or not native_session_id or
            len(native_session_id) > 256 or
            any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in native_session_id)):
        _stop("D6_BIND_NATIVE_SESSION_INPUT_INVALID")
    if workspace_root != Path(trust["tasks_root"]) and not os.environ.get("LLMTOOL_D6_TESTING"):
        _stop("D6_BIND_NATIVE_SESSION_WORKSPACE_INVALID")
    try:
        canonical_root = Path(canonical_root).resolve(strict=True)
        workspace_root = Path(os.path.realpath(workspace_root))
    except (OSError, TypeError):
        _stop("D6_BIND_NATIVE_SESSION_ROOT_MISMATCH")
    if (_task_git(canonical_root, "rev-parse", "--show-toplevel").stdout.strip() !=
            str(canonical_root)):
        _stop("D6_BIND_NATIVE_SESSION_CANONICAL_ROOT")
    common_dir = Path(_task_git(canonical_root, "rev-parse", "--path-format=absolute",
                                "--git-common-dir").stdout.strip())
    if (not common_dir.is_absolute() or common_dir.name != ".git" or
            Path(os.path.realpath(common_dir)) != common_dir):
        _stop("D6_BIND_NATIVE_SESSION_COMMON_DIR_MISMATCH")
    registry = common_dir / "llmtool-worktrees"
    _safe_dir(registry, mode=0o700, uid=os.getuid())
    try:
        lock_fd = os.open(registry / ".lifecycle.lock",
                          os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        lock_info = os.fstat(lock_fd)
        if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid() or
                stat.S_IMODE(lock_info.st_mode) != 0o600 or lock_info.st_nlink != 1):
            os.close(lock_fd)
            _stop("D6_REGISTRY_LOCK_INVALID")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except LifecycleError:
        raise
    except OSError:
        _stop("D6_REGISTRY_LOCK_UNAVAILABLE")
    try:
        trust = _recheck_trust_after_lock(trust)
        record_path = registry / (task_id + ".json")
        if not os.path.lexists(record_path):
            _stop("D6_BIND_NATIVE_SESSION_UNKNOWN_TASK")
        record = _read_registry_record(record_path)
        if record.get("task_id") != task_id:
            _stop("D6_BIND_NATIVE_SESSION_UNKNOWN_TASK")
        if record.get("state") in ("RETAIN_BLOCKED", "REMOVED"):
            _stop("D6_BIND_NATIVE_SESSION_STATE_REFUSED")
        if record.get("state") not in ("ACTIVE", "REPAIR") or record.get("lock_state") != "LOCKED":
            _stop("D6_BIND_NATIVE_SESSION_STATE_REFUSED")
        try:
            root = Path(record.get("root_realpath")).resolve(strict=True)
            root_info = root.lstat()
        except (OSError, TypeError):
            _stop("D6_BIND_NATIVE_SESSION_ROOT_MISMATCH")
        registered_identity = record.get("physical_identity")
        if (str(root) != record.get("root_realpath") or root.parent != workspace_root or
                type(registered_identity) is not dict or
                (root_info.st_dev, root_info.st_ino, root_info.st_uid) !=
                (registered_identity.get("device"), registered_identity.get("inode"),
                 registered_identity.get("uid")) or
                root_info.st_uid != os.getuid() or not stat.S_ISDIR(root_info.st_mode) or
                stat.S_IMODE(root_info.st_mode) != 0o700 or
                registered_identity.get("mode") != "0o700"):
            _stop("D6_BIND_NATIVE_SESSION_ROOT_MISMATCH")
        if record.get("git_common_dir") != str(common_dir):
            _stop("D6_BIND_NATIVE_SESSION_COMMON_DIR_MISMATCH")
        if (_task_git(root, "rev-parse", "--show-toplevel").stdout.strip() != str(root) or
                _task_git(root, "rev-parse", "--path-format=absolute",
                          "--git-common-dir").stdout.strip() != str(common_dir)):
            _stop("D6_BIND_NATIVE_SESSION_ROOT_MISMATCH")
        _require_trust_binding(record, trust)
        branch = record.get("branch")
        if (type(branch) is not str or not _valid_branch(canonical_root, branch) or
                _task_git(root, "branch", "--show-current").stdout.strip() != branch):
            _stop("D6_BIND_NATIVE_SESSION_BRANCH_MISMATCH")
        current = record.get("native_session_id")
        if current not in (None, "UNKNOWN"):
            _stop("D6_BIND_NATIVE_SESSION_ALREADY_SET")
        updated = dict(record)
        updated["native_session_id"] = native_session_id
        if (type(updated.get("session_ref")) is str and
                updated["session_ref"].startswith("pending-")):
            updated["session_ref"] = native_session_id
        _replace_record(record_path, updated)
        print("D6_NATIVE_SESSION_BOUND task_id=" + task_id)
        return native_session_id
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


# Retire accepts only records the PM can still act on; REVIEW_PENDING,
# RETAIN_BLOCKED, REMOVED and hand-written states are kept for a human. A
# PM-released expired lease (LEASE_RELEASED) still passes every retire gate.
RETIRE_STATES = frozenset(("ACTIVE", "REPAIR", "RETIRE_READY", "INTEGRATED",
                           LEASE_RELEASED_STATE))
# Dry runs show at most this many unknown paths; evidence may cover no more,
# so the PM never accepts a path the output did not show.
UNKNOWN_DISPLAY_CAP = 50
CONTENT_REASONS = frozenset((
    "D6_RETIRE_UNKNOWN_LOCAL_STATE", "D6_RETIRE_RETENTION_PATH_INVALID",
    "D6_RETIRE_RETENTION_PATH_SYMLINK", "D6_RETIRE_RETENTION_PATH_TRACKED",
    "D6_RETIRE_RETENTION_PATH_UNAVAILABLE"))
WORKTREE_GIT_LOCKS = ("index.lock", "HEAD.lock", "ORIG_HEAD.lock")
COMMON_GIT_LOCKS = ("packed-refs.lock", "config.lock", "shallow.lock")
LSOF_CANDIDATES = ("/usr/sbin/lsof", "/usr/bin/lsof")
SHA40_RE = re.compile(r"[0-9a-f]{40}\Z")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _lease_expiry(now=None):
    """UTC lease expiry LEASE_SECONDS after ``now`` (epoch seconds, default now)."""
    now = time.time() if now is None else now
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(now) + LEASE_SECONDS))


def _parse_utc(text):
    """Epoch seconds of a strict ``YYYY-MM-DDTHH:MM:SSZ`` value, or None."""
    if type(text) is not str or not LEASE_TIME_RE.fullmatch(text):
        return None
    try:
        return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        return None


def lease_status(record, now=None):
    """Read-only writer-lease status of one D6 record; never changes it.

    ``LEASE_RELEASED`` (a PM released it), ``LEASE_UNSET`` (a record written
    before leases existed; it never expires), ``LEASE_INVALID`` (unparsable
    value; never treated as expired), ``LEASE_EXPIRED`` or ``LEASE_ACTIVE``.
    """
    if record.get("state") == LEASE_RELEASED_STATE:
        return "LEASE_RELEASED"
    if "lease_expires_at" not in record:
        return "LEASE_UNSET"
    expires = _parse_utc(record["lease_expires_at"])
    if expires is None:
        return "LEASE_INVALID"
    now = time.time() if now is None else now
    return "LEASE_EXPIRED" if now >= expires else "LEASE_ACTIVE"


def _task_git(cwd, *args, check=True, timeout=GIT_TIMEOUT_SECONDS):
    """Git for retire: task-tree hooks, fsmonitor and auto-gc never run."""
    return _run_git(cwd, "--no-optional-locks", "--no-replace-objects",
                    "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                    "-c", "gc.auto=0", *args, check=check, timeout=timeout)


def _resolve_lsof():
    """First fixed lsof path that is a safe regular file, or None."""
    import os
    import stat
    try:
        uid = os.getuid()
    except AttributeError:
        return None
    for path in LSOF_CANDIDATES:
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        if st.st_uid not in (0, uid):
            continue
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            continue
        return path
    return None


def _process_cwds():
    """(pid, cwd) for every visible process, or None when lsof gives no answer."""
    lsof = _resolve_lsof()
    if lsof is None:
        return None
    try:
        result = subprocess.run(
            [lsof, "-n", "-P", "-w", "-d", "cwd", "-Fpn"],
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


LSOF_TIMEOUT_SECONDS = 60
LSOF_PID_RE = re.compile(r"[0-9]+\Z")


def _process_open_files():
    """Every file lsof shows for every visible process, for the lease release.

    Unlike the cwd-only retire read, this lists all descriptor kinds: cwd,
    root directory, executable text (``txt``), memory-mapped files (``mem``,
    ``mmap``) and numbered open descriptors. Returns ``(entries, None)`` with
    ``(pid, fd, name)`` triples, or ``(None, code)`` when the answer is not
    complete: ``LSOF_UNAVAILABLE``, ``LSOF_START_FAILED``, ``LSOF_TIMEOUT``,
    ``LSOF_FAILED`` (non-zero exit) or ``LSOF_OUTPUT_INVALID``. An unknown
    answer is never read as "no process".
    """
    lsof = _resolve_lsof()
    if lsof is None:
        return None, "LSOF_UNAVAILABLE"
    try:
        result = subprocess.run(
            [lsof, "-n", "-P", "-w", "-Fpfn"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=LSOF_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return None, "LSOF_TIMEOUT"
    except OSError:
        return None, "LSOF_START_FAILED"
    if result.returncode != 0:
        return None, "LSOF_FAILED"
    if type(result.stdout) is not bytes:
        return None, "LSOF_OUTPUT_INVALID"
    entries = []
    pids = set()
    pid = fd = None
    for line in result.stdout.decode("utf-8", "surrogateescape").splitlines():
        field, value = line[:1], line[1:]
        if field == "p":
            if not LSOF_PID_RE.fullmatch(value):
                return None, "LSOF_OUTPUT_INVALID"
            pid, fd = value, None
            pids.add(value)
        elif field == "f":
            if pid is None:
                return None, "LSOF_OUTPUT_INVALID"
            fd = value
        elif field == "n":
            if pid is None or fd is None:
                return None, "LSOF_OUTPUT_INVALID"
            entries.append((pid, fd, value))
        # Any other field letter, or a blank line, carries no path and is ignored.
    # This process is always visible to itself, so a listing without it is
    # partial or not lsof output at all.
    if str(os.getpid()) not in pids:
        return None, "LSOF_OUTPUT_INVALID"
    return entries, None


def _path_parts(path):
    """Whole components of an absolute path (lexically normalised), or None."""
    if type(path) is not str or not path.startswith("/"):
        return None
    return tuple(part for part in os.path.normpath(path).split("/") if part)


def _worktree_holders(root):
    """``(sorted PIDs, None)`` using the worktree, or ``(None, code)`` if unknown.

    A process counts when any of its lsof entries (cwd, root directory,
    executable, mapped file or open descriptor) names the worktree root or a
    path inside it. The match is on whole components against both the given
    root and its resolved form (lsof reports kernel paths, for example
    ``/private/var`` for ``/var``), so a sibling such as ``<root>-foo`` never
    counts. Non-path names (pipes, sockets) never match.
    """
    entries, error = _process_open_files()
    if error is not None:
        return None, error
    roots = set()
    for candidate in (str(root), os.path.realpath(str(root))):
        parts = _path_parts(candidate)
        if parts is None:
            return None, "D6_WORKTREE_ROOT_UNRESOLVED"
        roots.add(parts)
    holders = set()
    for pid, _fd, name in entries:
        parts = _path_parts(name)
        if parts is None:
            continue
        if any(parts[:len(prefix)] == prefix for prefix in roots):
            holders.add(pid)
    return sorted(holders, key=int), None


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
    root_real = os.path.realpath(root)
    matches = []
    for stanza in raw.split("\n\n"):
        fields = stanza.splitlines()
        if (fields and fields[0].startswith("worktree ") and
                os.path.realpath(fields[0][9:]) == root_real):
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
    current_branch = _task_git(root, "branch", "--show-current").stdout.strip()
    detached_review = (type(branch) is str and branch.startswith("review/") and
                       _valid_branch(canonical_root, branch) and not current_branch)
    if (type(branch) is not str or not _valid_branch(canonical_root, branch) or
            (current_branch != branch and not detached_review)):
        return ["D6_RETIRE_BRANCH_MISMATCH"]
    return []


def _check_merge_evidence(root, head, squash, train_squash=None):
    """Return the merge proof rule name if head is proven merged, else None."""
    def is_ancestor(commit, ref):
        return _task_git(root, "merge-base", "--is-ancestor", commit, ref,
                         check=False).returncode == 0

    def has_ref(ref):
        return _task_git(root, "rev-parse", "--verify", "--quiet",
                         ref + "^{commit}", check=False).returncode == 0

    origin_main = "refs/remotes/origin/main"
    if not has_ref(origin_main):
        return None
    if squash is not None:
        pr_head, squash_sha = squash
        parents = _task_git(root, "rev-list", "--parents", "-n", "1", squash_sha,
                            check=False)
        if (pr_head == head and parents.returncode == 0 and
                len(parents.stdout.split()) == 2 and is_ancestor(squash_sha, origin_main)):
            return "squash_evidence"
    if train_squash is not None:
        pr_head, train_head, squash_sha = train_squash
        parents = _task_git(root, "rev-list", "--parents", "-n", "1", squash_sha,
                            check=False)
        squash_tree = _task_git(root, "rev-parse", "--verify", "--quiet",
                                squash_sha + "^{tree}", check=False)
        train_tree = _task_git(root, "rev-parse", "--verify", "--quiet",
                               train_head + "^{tree}", check=False)
        if (pr_head == head and is_ancestor(pr_head, train_head) and
                parents.returncode == 0 and len(parents.stdout.split()) == 2 and
                is_ancestor(squash_sha, origin_main) and
                squash_tree.returncode == 0 and train_tree.returncode == 0 and
                squash_tree.stdout == train_tree.stdout):
            return "train_squash_evidence"
    return None


def _publication(root, branch, head, squash, train_squash=None):
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
    refs = _task_git(root, "for-each-ref", "--format=%(refname)",
                     "refs/remotes/origin/").stdout.splitlines()
    # The origin fetch refspec maps PR source branches to ordinary tracking refs.
    for ref in refs:
        if ref in (origin_branch, origin_main, "refs/remotes/origin/HEAD"):
            continue
        if has_ref(ref) and is_ancestor(head, ref):
            return "origin_pr_head"
    if not has_ref(origin_main):
        return None
    if is_ancestor(head, origin_main):
        return "origin_main"
    return _check_merge_evidence(root, head, squash, train_squash)


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


def _is_reviewer_record(record):
    """Reviewer D6s are explicitly marked and use the dedicated review branch."""
    branch = record.get("branch")
    agent_id = record.get("trusted_agent_id", "")
    marked = (type(agent_id) is str and "reviewer" in agent_id.casefold())
    marked = marked or record.get("role") == "reviewer" or record.get("reviewer") is True
    return marked and type(branch) is str and branch.startswith("review/")


def _retire_plan(task_id, record, *, canonical_root, common_dir, workspace_root, squash,
                 train_squash=None,
                 discard_uncommitted=False, unknown_evidence=None,
                 reviewed_pr_evidence=None, trust=None):
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
    trust_status = _trust_binding_status(record, _load_trust() if trust is None else trust)
    identity_workspace = workspace_root
    if trust_status == "rotated":
        # A rotation may have moved tasks_root: check the tree at its validated
        # recorded root, under the record's own tasks_root (7.3). An invalid
        # recorded root falls back to the current workspace, whose identity
        # gates then name the failure.
        recorded_root = _rotated_record_root(record, task_id, canonical_root, common_dir)
        if recorded_root is not None:
            root, identity_workspace = recorded_root, recorded_root.parent
            plan["root"] = str(root)
    reasons = _identity_reasons(root, record, canonical_root, common_dir, identity_workspace)
    if reasons:
        plan["reasons"].extend(reasons)
        return plan
    # A rotated trust binding never lets retire remove the tree; the caller
    # turns this sole reason into a durable RETAIN_BLOCKED on execute (7.3).
    if trust_status == "rotated":
        plan["reasons"].append("D6_TRUST_ROTATED")
        plan["trust_rotated"] = True
        return plan
    if trust_status == "legacy":
        plan["legacy_unstamped"] = True
    branch = record["branch"]
    head = _task_git(root, "rev-parse", "HEAD").stdout.strip()
    plan.update(branch=branch, head=head)
    listing = _worktree_listing(canonical_root, root)
    detached_review = branch.startswith("review/") and "detached" in (listing or {})
    if (listing is None or listing.get("HEAD") != head or
            (listing.get("branch") != "refs/heads/" + branch and not detached_review) or
            "prunable" in listing):
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
    reviewer_record = _is_reviewer_record(record)
    if reviewed_pr_evidence is not None and not reviewer_record:
        plan["reasons"].append("D6_RETIRE_REVIEW_EVIDENCE_NOT_APPLICABLE")
    if reviewer_record:
        if reviewed_pr_evidence is None:
            plan["reasons"].append("D6_RETIRE_REVIEW_EVIDENCE_REQUIRED")
        else:
            pr_number, pr_head = reviewed_pr_evidence
            if pr_head == head:
                if record.get("head_sha") != head:
                    plan["reasons"].append("D6_RETIRE_REVIEW_HEAD_MISMATCH")
                else:
                    publication = _publication(root, branch, head, None, None)
                    if publication not in ("origin_branch", "origin_pr_head"):
                        plan["reasons"].append("D6_RETIRE_UNPUBLISHED")
                    else:
                        plan["publication"] = "reviewed_pr_head"
                        plan["reviewed_pr_number"] = pr_number
            else:
                is_ancestor = (_task_git(root, "merge-base", "--is-ancestor", head, pr_head,
                                         check=False).returncode == 0)
                if record.get("head_sha") != head or not is_ancestor:
                    plan["reasons"].append("D6_RETIRE_REVIEW_HEAD_MISMATCH")
                else:
                    merge_proof = _check_merge_evidence(root, pr_head, squash, train_squash)
                    if merge_proof is None:
                        plan["reasons"].append("D6_RETIRE_REVIEW_MERGE_UNPROVEN")
                    else:
                        plan["publication"] = "reviewed_pr_merged_descendant"
                        plan["reviewed_pr_number"] = pr_number
    else:
        plan["publication"] = _publication(root, branch, head, squash, train_squash)
        if plan["publication"] is None:
            plan["reasons"].append("D6_RETIRE_UNPUBLISHED")
    if _task_git(root, "status", "--porcelain=v1", "-z",
                 "--untracked-files=no").stdout:
        tracked = _tracked_changes(root)
        if (discard_uncommitted and not reviewer_record and tracked and
                all(map(_safe_relative, tracked))):
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
    if discard_uncommitted and not reviewer_record:
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
        plan["unknown_paths"] = unknown[:UNKNOWN_DISPLAY_CAP]
        plan["unknown_count"] = len(unknown)
        plan["unknown_truncated"] = len(unknown) > UNKNOWN_DISPLAY_CAP
    if unknown_evidence is not None:
        if len(unknown) > UNKNOWN_DISPLAY_CAP:
            plan["reasons"].append("D6_RETIRE_EVIDENCE_TRUNCATED")
        elif unknown_evidence == plan["unknown_digest"]:
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
                plan["failure_recorded"] = True
            except LifecycleError as exc:
                reasons.append(exc.code)
        return reasons
    if os.path.lexists(root) or _worktree_listing(canonical_root, root) is not None:
        return ["D6_RETIRE_REMOVE_UNVERIFIED"]
    # The tree is gone from here on; a registry failure is reported, not raised.
    plan["tree_removed"] = True
    try:
        if _read_registry_record(record_path) != record:
            return ["D6_REGISTRY_CHANGED"]
    except LifecycleError as exc:
        return [exc.code]
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
    try:
        _replace_record(record_path, updated)
    except LifecycleError as exc:
        return [exc.code]
    return []


def _rotation_note(record, trust):
    """Evidence text for a record whose trust binding no longer matches."""
    binding = record.get("trust_binding_sha256")
    if "trust_sha256" not in record and "trust_binding_sha256" not in record:
        label = "legacy-unstamped"
    elif type(binding) is str and re.fullmatch(r"[0-9a-f]{64}", binding):
        label = binding
    else:
        label = "invalid"
    return (" (trust rotation: record binding " + label + " != current " +
            trust["binding_sha256"] + "; tree kept, retire and recreate)")


def _mark_retain_blocked(record_path, record, reasons, note="", action="Retire"):
    updated = dict(record)
    now = _now()
    updated.update(state="RETAIN_BLOCKED", last_verified_at=now,
                   evidence_refs=list(record.get("evidence_refs") or []) + [
                       action + " refused " + now + ": " + ",".join(reasons) + note])
    _replace_record(record_path, updated)


REBIND_STATES = frozenset(("ACTIVE", "REPAIR", "RETIRE_READY", "INTEGRATED"))


def rebind_device_tasks(*, canonical_root, task_ids, execute=False,
                        workspace_root=None):
    """Audit and optionally repair only a D6 record's changed device number."""
    trust = _load_trust()
    if (not task_ids or len(task_ids) != len(set(task_ids)) or
            any(not TASK_ID_RE.fullmatch(task_id or "") for task_id in task_ids)):
        _stop("D6_TASK_INPUT_INVALID")
    if workspace_root is None:
        workspace_root = trust["tasks_root"]
    workspace_root = Path(workspace_root)
    if workspace_root != Path(trust["tasks_root"]) and not os.environ.get("LLMTOOL_D6_TESTING"):
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
    if _task_git(canonical_root, "remote", "get-url", "origin").stdout.strip() not in trust["origins"]:
        _stop("D6_ORIGIN_INVALID")
    registry = common_dir / "llmtool-worktrees"
    _safe_dir(registry, mode=0o700, uid=os.getuid())
    lock_fd = None
    if execute:
        try:
            lock_fd = os.open(registry / ".lifecycle.lock",
                              os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            lock_info = os.fstat(lock_fd)
            if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid() or
                    stat.S_IMODE(lock_info.st_mode) != 0o600 or lock_info.st_nlink != 1):
                os.close(lock_fd)
                lock_fd = None
                _stop("D6_REGISTRY_LOCK_INVALID")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except LifecycleError:
            raise
        except OSError:
            _stop("D6_REGISTRY_LOCK_UNAVAILABLE")

    def assess(task_id, record, *, require_device_mismatch=True):
        if (type(record) is not dict or type(record.get("schema_version")) is not int or
                record.get("schema_version") != 1 or record.get("task_id") != task_id or
                type(record.get("root_realpath")) is not str or
                type(record.get("physical_identity")) is not dict or
                set(record["physical_identity"]) != {"device", "inode", "uid", "mode"} or
                any(type(record["physical_identity"].get(key)) is not int or
                    record["physical_identity"][key] < 0
                    for key in ("device", "inode", "uid")) or
                record["physical_identity"].get("mode") != "0o700" or
                type(record.get("evidence_refs")) is not list or
                any(type(item) is not str for item in record.get("evidence_refs", [])) or
                type(record.get("state")) is not str or
                type(record.get("branch")) is not str or
                type(record.get("base_sha")) is not str or
                not SHA40_RE.fullmatch(record["base_sha"]) or
                type(record.get("head_sha")) is not str or
                not SHA40_RE.fullmatch(record["head_sha"])):
            return "D6_REBIND_RECORD_SCHEMA_INVALID", None
        if record.get("state") not in REBIND_STATES:
            return "D6_REBIND_STATE_REFUSED", None
        if record.get("lock_state") != "LOCKED":
            return "D6_REBIND_LOCK_STATE_MISMATCH", None
        if _trust_binding_status(record, trust) == "rotated":
            return "D6_TRUST_ROTATED", None
        root_text = record["root_realpath"]
        try:
            root = Path(root_text)
            resolved = root.resolve(strict=True)
        except (OSError, RuntimeError):
            return "D6_REBIND_PATH_MISMATCH", None
        if (str(resolved) != root_text or str(resolved) == str(canonical_root) or
                resolved.parent != workspace_root):
            return "D6_REBIND_PATH_MISMATCH", None
        try:
            info = resolved.lstat()
        except OSError:
            return "D6_REBIND_PATH_MISMATCH", None
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            return "D6_REBIND_PATH_MISMATCH", None
        identity = record["physical_identity"]
        if info.st_ino != identity["inode"]:
            return "D6_REBIND_INODE_MISMATCH", None
        if info.st_uid != identity["uid"] or info.st_uid != os.getuid():
            return "D6_REBIND_UID_MISMATCH", None
        if stat.S_IMODE(info.st_mode) != 0o700 or identity["mode"] != "0o700":
            return "D6_REBIND_MODE_MISMATCH", None
        if record.get("git_common_dir") != str(common_dir):
            return "D6_REBIND_COMMON_DIR_MISMATCH", None
        if (_task_git(resolved, "rev-parse", "--show-toplevel").stdout.strip() != str(resolved) or
                _task_git(resolved, "rev-parse", "--path-format=absolute",
                          "--git-common-dir").stdout.strip() != str(common_dir)):
            return "D6_REBIND_COMMON_DIR_MISMATCH", None
        branch = record["branch"]
        if (not _valid_branch(canonical_root, branch) or
                _task_git(resolved, "branch", "--show-current").stdout.strip() != branch):
            return "D6_REBIND_BRANCH_MISMATCH", None
        head = _task_git(resolved, "rev-parse", "--verify", "HEAD").stdout.strip()
        if (_task_git(resolved, "merge-base", "--is-ancestor", record["head_sha"],
                      head, check=False).returncode != 0):
            return "D6_REBIND_HEAD_MISMATCH", None
        if require_device_mismatch and info.st_dev == identity["device"]:
            return "D6_REBIND_DEVICE_UNCHANGED", None
        return None, (resolved, info, head)

    all_ok = True
    try:
        if execute:
            trust = _recheck_trust_after_lock(trust)
        for task_id in task_ids:
            result = {"task_id": task_id, "mode": "execute" if execute else "dry-run"}
            record_path = registry / (task_id + ".json")
            try:
                try:
                    record = _read_registry_record(record_path)
                except LifecycleError as exc:
                    if exc.code == "D6_REGISTRY_INVALID":
                        _stop("D6_REBIND_RECORD_SCHEMA_INVALID")
                    raise
                reason, current = assess(task_id, record)
                if reason:
                    raise LifecycleError(reason)
                old_device = record["physical_identity"]["device"]
                new_device = current[1].st_dev
                result.update(root=str(current[0]), old_device=old_device,
                              new_device=new_device)
                if execute:
                    updated = dict(record)
                    identity = dict(record["physical_identity"])
                    identity["device"] = new_device
                    updated["physical_identity"] = identity
                    evidence = "device rebind %s->%s at %s" % (
                        old_device, new_device, _now())
                    if current[2] != record["head_sha"]:
                        evidence += " (worktree HEAD %s ahead of record)" % current[2]
                    updated["evidence_refs"] = list(record["evidence_refs"]) + [
                        evidence]
                    _replace_record(record_path, updated)
                    confirmed = _read_registry_record(record_path)
                    verify_reason, _ = assess(task_id, confirmed,
                                              require_device_mismatch=False)
                    if (verify_reason or
                            confirmed.get("physical_identity", {}).get("device") != new_device or
                            confirmed.get("evidence_refs", [])[-1:] !=
                            updated["evidence_refs"][-1:]):
                        _stop("D6_REBIND_CONFIRMATION_FAILED")
                    result["result"] = "REBOUND"
                else:
                    result["result"] = "READY"
            except LifecycleError as exc:
                all_ok = False
                result["result"] = "BLOCKED"
                result["reasons"] = [exc.code]
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
    return all_ok


def retire_worktrees(*, canonical_root, task_ids, execute=False, squash_evidence=None,
                     train_evidence=None,
                     discard_uncommitted=(), unknown_evidence=None,
                     reviewed_pr_evidence=None,
                     workspace_root=None):
    """Plan (default) or execute gated retirement; print one JSON line per task.

    Returns True only when every task is ready (dry run) or removed.
    """
    trust = _load_trust()
    workspace_root = Path(trust["tasks_root"] if workspace_root is None else workspace_root)
    squash_evidence = squash_evidence or {}
    train_evidence = train_evidence or {}
    unknown_evidence = unknown_evidence or {}
    reviewed_pr_evidence = reviewed_pr_evidence or {}
    if (not task_ids or len(task_ids) != len(set(task_ids)) or
            any(not TASK_ID_RE.fullmatch(task_id or "") for task_id in task_ids) or
            not set(squash_evidence) <= set(task_ids) or
            not set(train_evidence) <= set(task_ids) or
            not set(unknown_evidence) <= set(task_ids) or
            not set(reviewed_pr_evidence) <= set(task_ids) or
            len(discard_uncommitted) != len(set(discard_uncommitted)) or
            not set(discard_uncommitted) <= set(task_ids)):
        _stop("D6_TASK_INPUT_INVALID")
    if workspace_root != Path(trust["tasks_root"]) and not os.environ.get("LLMTOOL_D6_TESTING"):
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
    if _task_git(canonical_root, "remote", "get-url", "origin").stdout.strip() not in trust["origins"]:
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
        if execute:
            trust = _recheck_trust_after_lock(trust)
        for task_id in task_ids:
            record_path = registry / (task_id + ".json")
            result = {"task_id": task_id, "mode": "execute" if execute else "dry-run"}
            try:
                record = _read_registry_record(record_path)
                plan = _retire_plan(task_id, record, canonical_root=canonical_root,
                                    common_dir=common_dir, workspace_root=workspace_root,
                                    squash=squash_evidence.get(task_id),
                                    train_squash=train_evidence.get(task_id),
                                    discard_uncommitted=task_id in discard_uncommitted,
                                    unknown_evidence=unknown_evidence.get(task_id),
                                    reviewed_pr_evidence=reviewed_pr_evidence.get(task_id),
                                    trust=trust)
            except LifecycleError as exc:
                record, plan = None, {"root": str(workspace_root / task_id),
                                      "reasons": [exc.code], "content_block": False,
                                      "targets": [], "unknown_paths": [],
                                      "discard_tracked": [], "discard_untracked": [],
                                      "pycache": []}
            result.update({key: plan[key] for key in (
                "root", "branch", "head", "publication", "live_pids", "unknown_paths",
                "unknown_count", "unknown_truncated", "unknown_digest",
                "reviewed_pr_number") if plan.get(key)})
            if plan.get("publication") == "origin_branch":
                result["publication_note"] = "pushed to origin/<branch>; merge not proven"
            elif plan.get("publication") == "origin_pr_head":
                result["publication_note"] = "HEAD is reachable from a published origin PR head ref"
            if plan.get("accepted_unknown"):
                result["unknown_accepted"] = True
            if plan.get("legacy_unstamped"):
                result["legacy_unstamped"] = True
            result["git_removes_retention_paths"] = [relative + ("/" if is_dir else "")
                                       for relative, is_dir in plan["targets"]]
            result["pycache_discard_count"] = len(plan["pycache"])
            uncommitted = sorted(plan["discard_tracked"] + plan["discard_untracked"])
            if uncommitted:
                result["discard_uncommitted_paths"] = uncommitted
            reasons = plan["reasons"]
            # RETAIN_BLOCKED is durable, so it is written only when every other
            # gate passed and unknown or unsafe content is the sole problem, or
            # when the record's trust binding rotated (the tree is kept, 7.3).
            trust_rotated = plan.get("trust_rotated") is True and record is not None
            retain_blocked = ((plan["content_block"] and record is not None and
                               set(reasons) <= CONTENT_REASONS) or trust_rotated)
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
                        _mark_retain_blocked(
                            record_path, _read_registry_record(record_path), reasons,
                            _rotation_note(record, trust) if trust_rotated else "")
                        result["registry_updated"] = True
                    except LifecycleError as exc:
                        result["reasons"].append(exc.code)
                elif plan.get("failure_recorded"):
                    result["registry_updated"] = True
                if plan.get("tree_removed"):
                    result["tree_removed"] = True
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


def _parse_train_evidence(values):
    evidence = {}
    for value in values or []:
        task_id, _sep, shas = value.partition("=")
        parts = shas.split(":")
        if (task_id in evidence or len(parts) != 3 or
                any(not SHA40_RE.fullmatch(sha) for sha in parts)):
            _stop("D6_TASK_INPUT_INVALID")
        evidence[task_id] = tuple(parts)
    return evidence


def reconcile_registry(*, canonical_root, execute=False, workspace_root=None):
    """Report missing-worktree registry entries, optionally retire their records."""
    trust = _load_trust()
    workspace_root = Path(trust["tasks_root"] if workspace_root is None else workspace_root)
    if workspace_root != Path(trust["tasks_root"]) and not os.environ.get("LLMTOOL_D6_TESTING"):
        _stop("D6_WORKSPACE_ROOT_INVALID")
    workspace_root = Path(os.path.realpath(workspace_root))
    canonical_root = Path(canonical_root).resolve(strict=True)
    if _task_git(canonical_root, "rev-parse", "--show-toplevel").stdout.strip() != str(canonical_root):
        _stop("D6_REPOSITORY_INVALID")
    common_dir = Path(_task_git(canonical_root, "rev-parse", "--path-format=absolute",
                                "--git-common-dir").stdout.strip())
    if (not common_dir.is_absolute() or common_dir.name != ".git" or
            Path(os.path.realpath(common_dir)) != common_dir or
            _task_git(canonical_root, "remote", "get-url", "origin").stdout.strip() not in trust["origins"]):
        _stop("D6_COMMON_DIR_INVALID")
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
    try:
        if execute:
            trust = _recheck_trust_after_lock(trust)
        raw = _task_git(canonical_root, "worktree", "list", "--porcelain").stdout
        listed = set()
        for stanza in raw.split("\n\n"):
            first = stanza.splitlines()[:1]
            if first and first[0].startswith("worktree "):
                listed.add(os.path.realpath(first[0][9:]))
        candidates = []
        for record_path in sorted(registry.glob("*.json")):
            try:
                record = _read_registry_record(record_path)
            except LifecycleError:
                continue
            task_id = record.get("task_id")
            if (type(task_id) is not str or not TASK_ID_RE.fullmatch(task_id) or
                    record_path.name != task_id + ".json" or
                    record.get("schema_version") != 1 or
                    record.get("state") in ("RETAIN_BLOCKED", "REMOVED",
                                             "RETIRED_EXTERNALLY") or
                    type(record.get("root_realpath")) is not str):
                continue
            trust_status = _trust_binding_status(record, trust)
            if trust_status == "rotated":
                # A rotation may have moved tasks_root: use the validated
                # recorded root so the record still reaches RETAIN_BLOCKED (7.3).
                root = _rotated_record_root(record, task_id, canonical_root, common_dir)
                if root is None:
                    continue
            else:
                root = workspace_root / task_id
                if os.path.realpath(record["root_realpath"]) != os.path.realpath(root):
                    continue
            if os.path.lexists(root) or os.path.realpath(root) in listed:
                continue
            candidates.append((task_id, record_path, record, trust_status))
        results = []
        for task_id, record_path, record, trust_status in candidates:
            result = {"task_id": task_id, "mode": "execute" if execute else "dry-run",
                      "result": "RETIRED_EXTERNALLY" if execute else "RECONCILE_READY"}
            if trust_status == "legacy":
                result["legacy_unstamped"] = True
            if trust_status == "rotated":
                # A rotated record is never retired by reconcile. Execute keeps
                # it as RETAIN_BLOCKED, so only --release-retain with an Owner
                # approval from the current trust can release it (7.3).
                result.update(result="BLOCKED", reason="D6_TRUST_ROTATED")
                if execute:
                    if _read_registry_record(record_path) != record:
                        result["reason"] = "D6_REGISTRY_CHANGED"
                    else:
                        _mark_retain_blocked(record_path, record, ["D6_TRUST_ROTATED"],
                                             _rotation_note(record, trust), "Reconcile")
                        result["result"] = "RETAIN_BLOCKED"
            elif execute:
                # Recheck both independent absence signals while holding the registry lock.
                latest_listing = _task_git(canonical_root, "worktree", "list",
                                           "--porcelain").stdout
                latest_paths = {line[9:] for line in latest_listing.splitlines()
                                if line.startswith("worktree ")}
                if (os.path.lexists(workspace_root / task_id) or
                        os.path.realpath(workspace_root / task_id) in
                        {os.path.realpath(path) for path in latest_paths}):
                    result["result"] = "SKIPPED_EXISTS"
                elif _read_registry_record(record_path) != record:
                    result["result"] = "BLOCKED"
                    result["reason"] = "D6_REGISTRY_CHANGED"
                elif record.get("state") == "RETAIN_BLOCKED":
                    result["result"] = "SKIPPED_RETAIN_BLOCKED"
                else:
                    now = _now()
                    updated = dict(record)
                    updated.update(state="RETIRED_EXTERNALLY", last_verified_at=now,
                                   retired_externally_at=now,
                                   disposition="Worktree directory absent and not registered with Git at " + now)
                    _replace_record(record_path, updated)
                    result["retired_externally_at"] = now
                    result["reason"] = "worktree directory absent and not in git worktree list"
            else:
                result["reason"] = "worktree directory absent and not in git worktree list"
            results.append(result)
        print(json.dumps({"mode": "execute" if execute else "dry-run",
                          "records": results}, sort_keys=True))
        return True
    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)


def _lease_registry(canonical_root, trust):
    """Validated canonical root, common dir and owner-only registry directory."""
    canonical_root = Path(canonical_root).resolve(strict=True)
    if _task_git(canonical_root, "rev-parse", "--show-toplevel").stdout.strip() != str(canonical_root):
        _stop("D6_REPOSITORY_INVALID")
    common_dir = Path(_task_git(canonical_root, "rev-parse", "--path-format=absolute",
                                "--git-common-dir").stdout.strip())
    if (not common_dir.is_absolute() or common_dir.name != ".git" or
            Path(os.path.realpath(common_dir)) != common_dir or
            _task_git(canonical_root, "remote", "get-url", "origin").stdout.strip() not in trust["origins"]):
        _stop("D6_COMMON_DIR_INVALID")
    registry = common_dir / "llmtool-worktrees"
    _safe_dir(registry, mode=0o700, uid=os.getuid())
    return canonical_root, common_dir, registry


def lease_status_report(*, canonical_root, task_ids):
    """Print each task's writer-lease status as one JSON line; read-only.

    Returns True when every record was read. An expired lease is only
    reported here; nothing is released or written.
    """
    trust = _load_trust()
    if (not task_ids or len(task_ids) != len(set(task_ids)) or
            any(not TASK_ID_RE.fullmatch(task_id or "") for task_id in task_ids)):
        _stop("D6_TASK_INPUT_INVALID")
    _canonical, _common, registry = _lease_registry(canonical_root, trust)
    all_ok = True
    for task_id in task_ids:
        result = {"task_id": task_id, "mode": "lease-status"}
        record_path = registry / (task_id + ".json")
        try:
            if not os.path.lexists(record_path):
                _stop("D6_LEASE_UNKNOWN_TASK")
            record = _read_registry_record(record_path)
            if record.get("task_id") != task_id:
                _stop("D6_LEASE_UNKNOWN_TASK")
            status = lease_status(record)
            result.update(state=record.get("state"), lease_status=status,
                          lease_expired=status == "LEASE_EXPIRED")
            if type(record.get("lease_expires_at")) is str:
                result["lease_expires_at"] = record["lease_expires_at"]
        except LifecycleError as exc:
            all_ok = False
            result["reason"] = exc.code
        print(json.dumps(result, sort_keys=True))
    return all_ok


def _valid_pm_session(value):
    return (type(value) is str and bool(value) and len(value) <= 256 and
            not any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value))


def release_expired_lease(*, canonical_root, task_id, pm_session, workspace_root=None):
    """PM release of one expired D6 writer lease; the tree and commits stay.

    Releases only when the record is ACTIVE/REPAIR, its lease is expired and
    no live process uses the worktree: not by cwd (the retire check) and not
    by an open, executable or memory-mapped file under it either. The
    record moves to LEASE_RELEASED, so the old writer can no longer record a
    result or resume; nothing in Git or the worktree changes. Prints one JSON
    line and returns True only when the lease was released.
    """
    trust = _load_trust()
    if not TASK_ID_RE.fullmatch(task_id or "") or not _valid_pm_session(pm_session):
        _stop("D6_TASK_INPUT_INVALID")
    workspace_root = Path(trust["tasks_root"] if workspace_root is None else workspace_root)
    if workspace_root != Path(trust["tasks_root"]) and not os.environ.get("LLMTOOL_D6_TESTING"):
        _stop("D6_WORKSPACE_ROOT_INVALID")
    workspace_root = Path(os.path.realpath(workspace_root))
    canonical_root, common_dir, registry = _lease_registry(canonical_root, trust)
    try:
        lock_fd = os.open(registry / ".lifecycle.lock",
                          os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        lock_info = os.fstat(lock_fd)
        if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid() or
                stat.S_IMODE(lock_info.st_mode) != 0o600 or lock_info.st_nlink != 1):
            os.close(lock_fd)
            _stop("D6_REGISTRY_LOCK_INVALID")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except LifecycleError:
        raise
    except OSError:
        _stop("D6_REGISTRY_LOCK_UNAVAILABLE")
    try:
        trust = _recheck_trust_after_lock(trust)
        record_path = registry / (task_id + ".json")
        result = {"task_id": task_id, "mode": "release-expired-lease", "result": "BLOCKED"}
        try:
            if not os.path.lexists(record_path):
                _stop("D6_LEASE_RELEASE_UNKNOWN_TASK")
            record = _read_registry_record(record_path)
            if record.get("schema_version") != 1 or record.get("task_id") != task_id:
                _stop("D6_LEASE_RELEASE_UNKNOWN_TASK")
            status = lease_status(record)
            result.update(state=record.get("state"), lease_status=status)
            if type(record.get("lease_expires_at")) is str:
                result["lease_expires_at"] = record["lease_expires_at"]
            if record.get("state") not in LEASE_RELEASE_STATES:
                _stop("D6_LEASE_RELEASE_STATE_REFUSED")
            _require_trust_binding(record, trust)
            if status == "LEASE_INVALID":
                _stop("D6_LEASE_RELEASE_LEASE_INVALID")
            if status != "LEASE_EXPIRED":
                # Includes LEASE_UNSET: a record written before leases existed
                # never expires and is never released here.
                _stop("D6_LEASE_RELEASE_NOT_EXPIRED")
            root = workspace_root / task_id
            reasons = _identity_reasons(root, record, canonical_root, common_dir,
                                        workspace_root)
            if reasons:
                result["identity_reasons"] = reasons
                _stop("D6_LEASE_RELEASE_IDENTITY_MISMATCH")
            # Final liveness check, under the registry lock and followed only
            # by the registry re-read and the record write: any process whose
            # cwd, executable, mapped file or open file is in the worktree
            # blocks, and an unknown answer never counts as "no process".
            live, liveness_error = _worktree_holders(root)
            if live is None:
                result["liveness_error"] = liveness_error
                _stop("D6_LEASE_RELEASE_LIVENESS_UNKNOWN")
            if live:
                result["live_pids"] = live
                _stop("D6_LEASE_RELEASE_LIVE_PROCESS")
            if _read_registry_record(record_path) != record:
                _stop("D6_REGISTRY_CHANGED")
            now = _now()
            updated = dict(record)
            updated.update(
                state=LEASE_RELEASED_STATE, last_verified_at=now,
                lease_released_at=now, lease_released_by=pm_session,
                lease_released_from_state=record["state"],
                evidence_refs=list(record.get("evidence_refs") or []) + [
                    "Expired writer lease (" + record["lease_expires_at"] +
                    ") released by PM " + now + "; no live process; worktree, "
                    "branch and commits kept"])
            _replace_record(record_path, updated)
            result.update(result="LEASE_RELEASED", state=LEASE_RELEASED_STATE,
                          lease_released_at=now, worktree_kept=True)
        except LifecycleError as exc:
            result["reason"] = exc.code
        print(json.dumps(result, sort_keys=True))
        return result["result"] == "LEASE_RELEASED"
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


# Legacy-only (#369): --release-retain now builds its pattern from trust.json
# (repository.slug and approval_refs.retain_release_issues, see
# _retain_release_re). This pre-trust value stays until PR-10 only so
# `llmtool trust verify-legacy` can compare against it.
OWNER_APPROVAL_RE = re.compile(
    r"https://github\.com/davidcatcat/davidcattoolsproject/issues/(?:289|352)"
    r"#issuecomment-[0-9]+\Z")


def _parse_release_retain(values, trust=None):
    approval_re = _retain_release_re(_load_trust() if trust is None else trust)
    approvals = {}
    for value in values or []:
        task_id, separator, approval = value.partition("=")
        if (not separator or not TASK_ID_RE.fullmatch(task_id or "") or
                task_id in approvals or not approval_re.fullmatch(approval)):
            _stop("D6_TASK_INPUT_INVALID")
        approvals[task_id] = approval
    if not approvals:
        _stop("D6_TASK_INPUT_INVALID")
    return approvals


def release_retained_records(*, canonical_root, approvals, workspace_root=None):
    """Release Owner-approved RETAIN_BLOCKED records for already absent trees.

    This is the release path for a record whose trust binding rotated (7.3),
    so a rotated record is reported, not refused: the gate is an Owner
    approval URL from the current trust's retain_release_issues.
    """
    trust = _load_trust()
    if type(approvals) is not dict or not approvals:
        _stop("D6_TASK_INPUT_INVALID")
    approval_re = _retain_release_re(trust)
    if any(not TASK_ID_RE.fullmatch(task_id or "") or
           type(approval) is not str or not approval_re.fullmatch(approval)
           for task_id, approval in approvals.items()):
        _stop("D6_TASK_INPUT_INVALID")
    if workspace_root is None:
        workspace_root = trust["tasks_root"]
    workspace_root = Path(workspace_root)
    if workspace_root != Path(trust["tasks_root"]) and not os.environ.get("LLMTOOL_D6_TESTING"):
        _stop("D6_WORKSPACE_ROOT_INVALID")
    workspace_root = Path(os.path.realpath(workspace_root))
    canonical_root = Path(canonical_root).resolve(strict=True)
    if _task_git(canonical_root, "rev-parse", "--show-toplevel").stdout.strip() != str(canonical_root):
        _stop("D6_REPOSITORY_INVALID")
    common_dir = Path(_task_git(canonical_root, "rev-parse", "--path-format=absolute",
                                "--git-common-dir").stdout.strip())
    if (not common_dir.is_absolute() or common_dir.name != ".git" or
            Path(os.path.realpath(common_dir)) != common_dir or
            _task_git(canonical_root, "remote", "get-url", "origin").stdout.strip() not in trust["origins"]):
        _stop("D6_COMMON_DIR_INVALID")
    registry = common_dir / "llmtool-worktrees"
    _safe_dir(registry, mode=0o700, uid=os.getuid())
    try:
        lock_fd = os.open(registry / ".lifecycle.lock",
                          os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except OSError:
        _stop("D6_REGISTRY_LOCK_UNAVAILABLE")
    try:
        trust = _recheck_trust_after_lock(trust)
        approval_re = _retain_release_re(trust)
        if any(not approval_re.fullmatch(approval) for approval in approvals.values()):
            _stop("D6_TASK_INPUT_INVALID")
        results = []
        for task_id, approval in sorted(approvals.items()):
            record_path = registry / (task_id + ".json")
            result = {"task_id": task_id, "result": "BLOCKED"}
            try:
                record = _read_registry_record(record_path)
                trust_status = _trust_binding_status(record, trust)
                if trust_status == "rotated":
                    result["trust_rotated"] = True
                elif trust_status == "legacy":
                    result["legacy_unstamped"] = True
                root = workspace_root / task_id
                if trust_status == "rotated":
                    # The old tree is checked at its validated recorded root,
                    # which may sit under a tasks_root the rotation moved (7.3).
                    recorded_root = _rotated_record_root(record, task_id,
                                                         canonical_root, common_dir)
                    if recorded_root is not None:
                        root = recorded_root
                if (record.get("schema_version") != 1 or
                        record.get("task_id") != task_id or
                        record.get("state") != "RETAIN_BLOCKED" or
                        record.get("root_realpath") != str(root)):
                    result["reason"] = "D6_RELEASE_RETAIN_RECORD_INVALID"
                else:
                    listing = _task_git(canonical_root, "worktree", "list",
                                        "--porcelain").stdout
                    listed = {os.path.realpath(line[9:]) for line in listing.splitlines()
                              if line.startswith("worktree ")}
                    if os.path.lexists(root) or os.path.realpath(root) in listed:
                        result["reason"] = "D6_RELEASE_RETAIN_WORKTREE_PRESENT"
                    elif _read_registry_record(record_path) != record:
                        result["reason"] = "D6_REGISTRY_CHANGED"
                    else:
                        now = _now()
                        updated = dict(record)
                        updated.update(
                            state="RETIRED_EXTERNALLY", last_verified_at=now,
                            retired_externally_at=now,
                            owner_approval_ref=approval,
                            disposition="Owner-approved release; worktree directory absent "
                            "and not listed by Git at " + now)
                        _replace_record(record_path, updated)
                        result.update(result="RETIRED_EXTERNALLY", approval_ref=approval,
                                      retired_externally_at=now)
            except LifecycleError as exc:
                result["reason"] = exc.code
            results.append(result)
        print(json.dumps({"mode": "release-retain", "records": results}, sort_keys=True))
        return all(item["result"] == "RETIRED_EXTERNALLY" for item in results)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


OWNER_APPROVED_RETAIN_RELEASE_RE = re.compile(
    r"https://github\.com/davidcatcat/davidcattoolsproject/issues/(?:289|529)"
    r"#issuecomment-[0-9]+\Z")


def _parse_owner_approved_retain_release(values):
    approvals = {}
    for value in values or []:
        task_id, separator, approval = value.partition("=")
        if (not separator or not TASK_ID_RE.fullmatch(task_id or "") or
                task_id in approvals or
                not OWNER_APPROVED_RETAIN_RELEASE_RE.fullmatch(approval)):
            _stop("D6_TASK_INPUT_INVALID")
        approvals[task_id] = approval
    if not approvals:
        _stop("D6_TASK_INPUT_INVALID")
    return approvals


def owner_approved_retain_release(*, canonical_root, approvals, execute=False,
                                  workspace_root=None):
    """Release Owner-approved RETAIN_BLOCKED records whose tree is absent.

    Only a directory that is absent at lstat time is eligible; any other
    result, including a symlink or an unreadable path, is refused. This is a
    dry run unless ``execute`` is true.
    """
    if (type(approvals) is not dict or not approvals or
            any(not TASK_ID_RE.fullmatch(task_id or "") or
                type(approval) is not str or
                not OWNER_APPROVED_RETAIN_RELEASE_RE.fullmatch(approval)
                for task_id, approval in approvals.items())):
        _stop("D6_TASK_INPUT_INVALID")
    trust = _load_trust()
    if workspace_root is None:
        workspace_root = trust["tasks_root"]
    workspace_root = Path(workspace_root)
    if workspace_root != Path(trust["tasks_root"]) and not os.environ.get("LLMTOOL_D6_TESTING"):
        _stop("D6_WORKSPACE_ROOT_INVALID")
    workspace_root = Path(os.path.realpath(workspace_root))
    canonical_root = Path(canonical_root).resolve(strict=True)
    if _task_git(canonical_root, "rev-parse", "--show-toplevel").stdout.strip() != str(canonical_root):
        _stop("D6_REPOSITORY_INVALID")
    common_dir = Path(_task_git(canonical_root, "rev-parse", "--path-format=absolute",
                                "--git-common-dir").stdout.strip())
    if (not common_dir.is_absolute() or common_dir.name != ".git" or
            Path(os.path.realpath(common_dir)) != common_dir or
            _task_git(canonical_root, "remote", "get-url", "origin").stdout.strip() not in trust["origins"]):
        _stop("D6_COMMON_DIR_INVALID")
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
    try:
        if execute:
            _recheck_trust_after_lock(trust)
        results = []
        for task_id, approval in sorted(approvals.items()):
            record_path = registry / (task_id + ".json")
            result = {"task_id": task_id,
                      "mode": "execute" if execute else "dry-run",
                      "result": "BLOCKED"}
            try:
                record = _read_registry_record(record_path)
                root = workspace_root / task_id
                if (record.get("schema_version") != 1 or
                        record.get("task_id") != task_id or
                        record.get("state") != "RETAIN_BLOCKED" or
                        record.get("root_realpath") != str(root)):
                    result["reason"] = "D6_RELEASE_RETAIN_RECORD_INVALID"
                else:
                    try:
                        root.lstat()
                    except FileNotFoundError:
                        absent = True
                    except OSError:
                        absent = False
                        result["reason"] = "D6_RELEASE_RETAIN_PATH_UNAVAILABLE"
                    else:
                        absent = False
                        result["reason"] = "D6_RELEASE_RETAIN_WORKTREE_PRESENT"
                    if absent and "reason" not in result:
                        if _read_registry_record(record_path) != record:
                            result["reason"] = "D6_REGISTRY_CHANGED"
                        elif not execute:
                            result.update(result="RELEASE_READY",
                                          approval_ref=approval)
                        else:
                            now = _now()
                            updated = dict(record)
                            updated.update(
                                state="RETIRED_EXTERNALLY",
                                last_verified_at=now,
                                retired_externally_at=now,
                                owner_approval_ref=approval,
                                disposition="Owner-approved retain release; worktree "
                                "directory absent at " + now)
                            _replace_record(record_path, updated)
                            result.update(result="RETIRED_EXTERNALLY",
                                          approval_ref=approval,
                                          retired_externally_at=now)
            except LifecycleError as exc:
                result["reason"] = exc.code
            results.append(result)
        print(json.dumps({"mode": "owner-approved-retain-release",
                          "execute": execute, "records": results}, sort_keys=True))
        return all(item["result"] in ("RELEASE_READY", "RETIRED_EXTERNALLY")
                   for item in results)
    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)


# Keep the alternate verb order available for callers that mirror the CLI name.
release_owner_approved_retain = owner_approved_retain_release


def _parse_unknown_evidence(values):
    evidence = {}
    for value in values or []:
        task_id, _sep, digest = value.partition("=")
        if task_id in evidence or not re.fullmatch(r"[0-9a-f]{64}", digest):
            _stop("D6_TASK_INPUT_INVALID")
        evidence[task_id] = digest
    return evidence


def _parse_reviewed_pr_evidence(values):
    evidence = {}
    for value in values or []:
        task_id, sep, binding = value.partition("=")
        pr_number, sep2, head_sha = binding.partition(":")
        if (not sep or not sep2 or task_id in evidence or
                not re.fullmatch(r"[1-9][0-9]*", pr_number) or
                not SHA40_RE.fullmatch(head_sha)):
            _stop("D6_TASK_INPUT_INVALID")
        evidence[task_id] = (pr_number, head_sha)
    return evidence


def _release_retain_help():
    """--release-retain help derived from trust; generic if trust is unusable."""
    text = ("release an absent RETAIN_BLOCKED record with an Owner approval "
            "comment URL from ")
    try:
        trust = _load_trust()
    except TrustError:
        trust = None
    issues = trust.get("retain_release_issues") if isinstance(trust, dict) else None
    slug = trust.get("slug") if isinstance(trust, dict) else None
    if issues and type(slug) is str:
        text += ("issue " + " or ".join(str(issue) for issue in issues) +
                 " of " + slug)
    else:
        text += "an issue in trust.json approval_refs.retain_release_issues"
    return text + " (repeatable)"


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
    parser.add_argument("--record-result-task-id",
                        help="record this clean committed descendant HEAD for the current D6 root")
    parser.add_argument("--bind-native-session-task-id")
    parser.add_argument("--execute-approved-lifecycle", action="store_true")
    parser.add_argument("--retire-task-id", action="append",
                        help="plan retirement of this task (repeatable); dry run by default")
    parser.add_argument("--rebind-device-task-id", action="append",
                        help="audit this task's device-only identity change (repeatable); dry run by default")
    parser.add_argument("--execute-approved-rebind", action="store_true",
                        help="write audited device-only D6 record rebinds")
    parser.add_argument("--squash-evidence", action="append",
                        metavar="TASK_ID=PR_HEAD_SHA:SQUASH_SHA",
                        help="merged squash PR: final PR head and its commit on origin/main")
    parser.add_argument("--train-evidence", action="append",
                        metavar="TASK_ID=PR_HEAD_SHA:TRAIN_HEAD_SHA:SQUASH_SHA",
                        help="merged squash train: PR head, train head and squash commit")
    parser.add_argument("--reviewed-pr-evidence", action="append",
                        metavar="TASK_ID=PR_NUMBER:HEAD_SHA",
                        help="closed or unmerged reviewer PR and its published head")
    parser.add_argument("--reconcile-registry", action="store_true",
                        help="list registry records whose worktree is absent; dry run by default")
    parser.add_argument("--execute-approved-reconcile", action="store_true",
                        help="mark absent worktree records RETIRED_EXTERNALLY")
    parser.add_argument("--release-retain", action="append",
                        metavar="TASK_ID=OWNER_APPROVAL_URL",
                        help=_release_retain_help())
    parser.add_argument("--owner-approved-retain-release", action="append",
                        metavar="TASK_ID=OWNER_COMMENT_URL",
                        help="release an absent RETAIN_BLOCKED record with an Owner approval "
                             "comment URL from issue 289 or 529 (repeatable); dry run by "
                             "default, --execute-approved-retire applies it")
    parser.add_argument("--discard-uncommitted", action="append", metavar="TASK_ID",
                        help="Owner-approved: discard this retire task's tracked and "
                             "untracked changes (repeatable); needs the execute flag to act")
    parser.add_argument("--retain-blocked-evidence", action="append",
                        metavar="TASK_ID=SHA256",
                        help="accept exactly the unknown paths whose digest the dry run "
                             "printed (also needed to retire a RETAIN_BLOCKED record)")
    parser.add_argument("--execute-approved-retire", action="store_true",
                        help="actually discard approved files and remove the worktrees")
    parser.add_argument("--lease-status-task-id", action="append", metavar="TASK_ID",
                        help="read-only: report this task's writer lease, including "
                             "LEASE_EXPIRED (repeatable)")
    parser.add_argument("--release-expired-lease", metavar="TASK_ID",
                        help="PM: release this task's expired writer lease when no live "
                             "process holds the worktree; needs --pm-session; the "
                             "worktree and commits are kept")
    parser.add_argument("--pm-session",
                        help="the PM session releasing an expired lease (recorded)")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--trust-sha256")
    args = parser.parse_args(argv)
    try:
        trust = _load_trust()
        if args.trust_sha256 is not None and args.trust_sha256 != trust["sha256"]:
            _stop("TRUST_CHANGED_DURING_RUN")
        if args.repo_root is None:
            args.repo_root = trust["canonical_root"]
        create_values = (args.task_id, args.agent_id, args.session_ref,
                         args.lease_ref, args.branch, args.base_sha)
        create_options = (args.github_login, args.native_session_id)
        if (args.lease_status_task_id is not None or
                args.release_expired_lease is not None or args.pm_session is not None):
            other_options = (
                any(value is not None for value in create_values + create_options) or
                args.resume_task_id is not None or args.resume_root is not None or
                args.record_result_task_id is not None or
                args.bind_native_session_task_id is not None or
                args.execute_approved_lifecycle or args.retire_task_id is not None or
                args.rebind_device_task_id is not None or args.execute_approved_rebind or
                args.squash_evidence or args.train_evidence or
                args.reviewed_pr_evidence or args.reconcile_registry or
                args.execute_approved_reconcile or args.release_retain or
                args.owner_approved_retain_release or args.discard_uncommitted or
                args.retain_blocked_evidence or args.execute_approved_retire)
            if args.release_expired_lease is not None:
                if (other_options or args.lease_status_task_id is not None or
                        args.pm_session is None):
                    _stop("D6_TASK_INPUT_INVALID")
                return 0 if release_expired_lease(
                    canonical_root=args.repo_root, task_id=args.release_expired_lease,
                    pm_session=args.pm_session) else 1
            if other_options or args.pm_session is not None:
                _stop("D6_TASK_INPUT_INVALID")
            return 0 if lease_status_report(
                canonical_root=args.repo_root,
                task_ids=args.lease_status_task_id) else 1
        if args.rebind_device_task_id is not None:
            if (any(value is not None for value in create_values + create_options) or
                    args.resume_task_id is not None or args.resume_root is not None or
                    args.record_result_task_id is not None or
                    args.bind_native_session_task_id is not None or
                    args.retire_task_id is not None or args.execute_approved_retire or
                    args.execute_approved_lifecycle or args.squash_evidence or
                    args.train_evidence or args.reviewed_pr_evidence or
                    args.discard_uncommitted or
                    args.retain_blocked_evidence or args.reconcile_registry or
                    args.execute_approved_reconcile or args.release_retain or
                    args.owner_approved_retain_release):
                _stop("D6_TASK_INPUT_INVALID")
            return 0 if rebind_device_tasks(
                canonical_root=args.repo_root,
                task_ids=args.rebind_device_task_id,
                execute=args.execute_approved_rebind) else 1
        if args.execute_approved_rebind:
            _stop("D6_TASK_INPUT_INVALID")
        if args.bind_native_session_task_id is not None:
            if (any(value is not None for value in create_values) or
                    args.github_login is not None or
                    args.native_session_id is None or args.resume_task_id is not None or
                    args.resume_root is not None or args.record_result_task_id is not None or
                    args.retire_task_id is not None or args.execute_approved_lifecycle or
                    args.execute_approved_retire or args.squash_evidence or
                    args.train_evidence or args.reviewed_pr_evidence or args.reconcile_registry or
                    args.execute_approved_reconcile or
                    args.discard_uncommitted or args.retain_blocked_evidence or
                    args.release_retain or args.owner_approved_retain_release):
                _stop("D6_TASK_INPUT_INVALID")
            bind_native_session(task_id=args.bind_native_session_task_id,
                                native_session_id=args.native_session_id,
                                canonical_root=args.repo_root)
            return 0
        if args.record_result_task_id is not None:
            if (any(value is not None for value in create_values + create_options) or
                    args.resume_task_id is not None or args.resume_root is not None or
                    args.retire_task_id is not None or args.execute_approved_lifecycle or
                    args.execute_approved_retire or args.squash_evidence or
                    args.train_evidence or args.reviewed_pr_evidence or args.reconcile_registry or
                    args.execute_approved_reconcile or
                    args.discard_uncommitted or args.retain_blocked_evidence or
                    args.release_retain or args.owner_approved_retain_release):
                _stop("D6_TASK_INPUT_INVALID")
            record_result(task_id=args.record_result_task_id,
                          canonical_root=args.repo_root)
            return 0
        if args.retire_task_id is not None:
            if (any(value is not None for value in create_values + create_options) or
                    args.resume_task_id is not None or args.resume_root is not None or
                    args.execute_approved_lifecycle or args.release_retain or
                    args.owner_approved_retain_release):
                _stop("D6_TASK_INPUT_INVALID")
            return 0 if retire_worktrees(
                canonical_root=args.repo_root, task_ids=args.retire_task_id,
                execute=args.execute_approved_retire,
                discard_uncommitted=args.discard_uncommitted or (),
                unknown_evidence=_parse_unknown_evidence(args.retain_blocked_evidence),
                squash_evidence=_parse_squash_evidence(args.squash_evidence),
                train_evidence=_parse_train_evidence(args.train_evidence),
                reviewed_pr_evidence=_parse_reviewed_pr_evidence(
                    args.reviewed_pr_evidence)) else 1
        if args.reconcile_registry or args.execute_approved_reconcile:
            if (args.retire_task_id is not None or args.execute_approved_retire or
                    args.squash_evidence or args.train_evidence or
                    args.reviewed_pr_evidence or
                    args.discard_uncommitted or args.retain_blocked_evidence or
                    args.release_retain or args.owner_approved_retain_release or
                    any(value is not None for value in create_values + create_options) or
                    args.resume_task_id is not None or args.resume_root is not None or
                    args.record_result_task_id is not None or
                    args.bind_native_session_task_id is not None or
                    args.execute_approved_lifecycle):
                _stop("D6_TASK_INPUT_INVALID")
            return 0 if reconcile_registry(canonical_root=args.repo_root,
                                           execute=args.execute_approved_reconcile) else 1
        if args.release_retain is not None:
            if (args.retire_task_id is not None or args.execute_approved_retire or
                    args.squash_evidence or args.train_evidence or
                    args.reviewed_pr_evidence or
                    args.discard_uncommitted or args.retain_blocked_evidence or
                    args.reconcile_registry or args.execute_approved_reconcile or
                    args.owner_approved_retain_release or
                    any(value is not None for value in create_values + create_options) or
                    args.resume_task_id is not None or args.resume_root is not None or
                    args.record_result_task_id is not None or
                    args.bind_native_session_task_id is not None or
                    args.execute_approved_lifecycle):
                _stop("D6_TASK_INPUT_INVALID")
            return 0 if release_retained_records(
                canonical_root=args.repo_root,
                approvals=_parse_release_retain(args.release_retain, trust)) else 1
        if args.owner_approved_retain_release is not None:
            if (args.retire_task_id is not None or args.squash_evidence or
                    args.train_evidence or args.discard_uncommitted or
                    args.retain_blocked_evidence or args.reconcile_registry or
                    args.execute_approved_reconcile or args.release_retain or
                    any(value is not None for value in create_values + create_options) or
                    args.resume_task_id is not None or args.resume_root is not None or
                    args.record_result_task_id is not None or
                    args.bind_native_session_task_id is not None or
                    args.execute_approved_lifecycle):
                _stop("D6_TASK_INPUT_INVALID")
            return 0 if owner_approved_retain_release(
                canonical_root=args.repo_root,
                approvals=_parse_owner_approved_retain_release(
                    args.owner_approved_retain_release),
                execute=args.execute_approved_retire) else 1
        if (args.execute_approved_retire or args.squash_evidence or args.train_evidence or
                args.reviewed_pr_evidence or
                args.discard_uncommitted or args.retain_blocked_evidence or
                args.release_retain):
            _stop("D6_TASK_INPUT_INVALID")
        if args.execute_approved_lifecycle:
            if (args.resume_task_id is None or args.resume_root is None or
                    any(value is not None for value in create_values + create_options) or
                    args.repo_root != trust["canonical_root"]):
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
    except TrustError as exc:
        print("STOP: D6 lifecycle rejected (" + exc.code + ")", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("STOP: D6 lifecycle rejected (D6_REPOSITORY_INVALID)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
