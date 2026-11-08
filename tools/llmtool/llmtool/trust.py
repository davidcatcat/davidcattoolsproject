"""Owner-only trust.json loader and repository identity check.

The loader is deliberately stdlib-only and does not invoke Git. Call
``verify_repository`` separately at a repository-sensitive entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
from types import MappingProxyType

MAX_TRUST_BYTES = 16 * 1024
MAX_JSON_NESTING = 32
_TRUST_CACHE = None
_HAS_DIR_FD_SUPPORT = (
    os.name == "posix" and hasattr(os, "O_DIRECTORY") and
    hasattr(os, "O_NOFOLLOW")
)
_SLUG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}\Z")
_WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9._-]{1,100}\.ya?ml\Z")
_RUNNER = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")


class TrustError(Exception):
    """Sanitized trust rejection; ``code`` is the only diagnostic detail."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TrustConfig:
    schema_version: int
    canonical_root: str
    workspace_root: str
    tasks_root: str
    repository: object
    ci_trust: object
    tools: object
    sha256: str
    binding_sha256: str


def _fail(code):
    raise TrustError(code)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("invalid constant")


def _check_json_nesting(text):
    """Reject excessive structural nesting independently of json's recursion limit."""
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_NESTING:
                raise ValueError("excessive nesting")
        elif char in "]}":
            depth = max(0, depth - 1)


def _exact(obj, keys):
    if type(obj) is not dict or set(obj) != set(keys):
        _fail("TRUST_SCHEMA_INVALID")


def _string(value, minimum=1, maximum=1024, controls=False):
    return (type(value) is str and minimum <= len(value) <= maximum and
            (controls or not any(ord(c) < 0x20 or ord(c) == 0x7f for c in value)))


def _list(value, minimum, maximum, predicate):
    return (type(value) is list and minimum <= len(value) <= maximum and
            all(predicate(item) for item in value) and len(set(value)) == len(value))


def _absolute_normalized(value):
    return type(value) is str and os.path.isabs(value) and os.path.normpath(value) == value


def _readonly(value):
    if type(value) is dict:
        return MappingProxyType({key: _readonly(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_readonly(item) for item in value)
    return value


def _hosted_label(item):
    return type(item) is str and _RUNNER.fullmatch(item) is not None and item != "self-hosted"


def _hosted_runner_labels(labels):
    """github-hosted runner_labels: a flat list of 1-8 labels, or 1-4 alternatives,
    each a flat list of 1-8 labels (#1253). No deeper nesting, no mixed forms.
    Alternatives need not be distinct; labels within one alternative must be."""
    if _list(labels, 1, 8, _hosted_label):
        return True
    return (type(labels) is list and 1 <= len(labels) <= 4 and
            all(_list(alternative, 1, 8, _hosted_label) for alternative in labels))


def _validate_schema(value):
    _exact(value, {"schema_version", "canonical_root", "workspace_root", "tasks_root",
                   "repository", "ci_trust", "tools"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _fail("TRUST_SCHEMA_VERSION_UNSUPPORTED")

    for key in ("canonical_root", "workspace_root", "tasks_root"):
        path = value[key]
        if (not _string(path) or not path.isascii() or
                any(ord(char) < 0x20 or ord(char) > 0x7e for char in path) or
                not _absolute_normalized(path)):
            _fail("TRUST_SCHEMA_INVALID")

    repository = value["repository"]
    _exact(repository, {"slug", "allowed_origins", "approval_refs"})
    slug = repository["slug"]
    if (type(slug) is not str or _SLUG.fullmatch(slug) is None or
            slug.split("/", 1)[1] in (".", "..")):
        _fail("TRUST_SCHEMA_INVALID")
    origins = repository["allowed_origins"]
    allowed = {f"git@github.com:{slug}.git", f"https://github.com/{slug}.git"}
    if not _list(origins, 1, 2, lambda item: type(item) is str and item in allowed):
        _fail("TRUST_SCHEMA_INVALID")
    refs = repository["approval_refs"]
    _exact(refs, {"reviewer_host_policy_issue", "worker_dispatch_issue",
                  "worker_dispatch_evidence_pr", "retain_release_issues"})
    for key in ("reviewer_host_policy_issue", "worker_dispatch_issue",
                "worker_dispatch_evidence_pr"):
        if type(refs[key]) is not int or not 1 <= refs[key] <= 10**9:
            _fail("TRUST_SCHEMA_INVALID")
    if not _list(refs["retain_release_issues"], 1, 8,
                 lambda item: type(item) is int and 1 <= item <= 10**9):
        _fail("TRUST_SCHEMA_INVALID")

    ci = value["ci_trust"]
    _exact(ci, {"mode", "github_actions_app_id", "workflows", "not_run_checks"})
    if ci["mode"] not in ("github-hosted", "self-hosted"):
        _fail("TRUST_CI_INVALID")
    if type(ci["github_actions_app_id"]) is not int or ci["github_actions_app_id"] != 15368:
        _fail("TRUST_SCHEMA_INVALID")
    if type(ci["workflows"]) is not list or not 1 <= len(ci["workflows"]) <= 4:
        _fail("TRUST_SCHEMA_INVALID")
    paths, checks = set(), set()
    has_code = has_docs = False
    for workflow in ci["workflows"]:
        _exact(workflow, {"path", "name", "runner_names", "runner_labels", "code_check", "docs_check"})
        if type(workflow["path"]) is not str or _WORKFLOW.fullmatch(workflow["path"]) is None:
            _fail("TRUST_SCHEMA_INVALID")
        if workflow["path"] in paths:
            _fail("TRUST_SCHEMA_INVALID")
        paths.add(workflow["path"])
        if not _string(workflow["name"], 1, 100):
            _fail("TRUST_SCHEMA_INVALID")
        if ci["mode"] == "self-hosted":
            if not _list(workflow["runner_names"], 1, 16,
                         lambda item: type(item) is str and _RUNNER.fullmatch(item) is not None):
                _fail("TRUST_CI_INVALID")
            labels = workflow["runner_labels"]
            if labels is not None and not _list(labels, 1, 8,
                    lambda item: type(item) is str and _RUNNER.fullmatch(item) is not None):
                _fail("TRUST_CI_INVALID")
        else:
            if workflow["runner_names"] is not None:
                _fail("TRUST_CI_INVALID")
            if not _hosted_runner_labels(workflow["runner_labels"]):
                _fail("TRUST_CI_INVALID")
        for field in ("code_check", "docs_check"):
            check = workflow[field]
            if check is not None and not _string(check, 1, 100):
                _fail("TRUST_SCHEMA_INVALID")
            if check is not None:
                if check in checks:
                    _fail("TRUST_CI_INVALID")
                checks.add(check)
                if field == "code_check":
                    has_code = True
                else:
                    has_docs = True
    if not has_code or not has_docs:
        _fail("TRUST_CI_INVALID")
    if not _list(ci["not_run_checks"], 0, 8, lambda item: _string(item, 1, 100)):
        _fail("TRUST_SCHEMA_INVALID")

    tools = value["tools"]
    _exact(tools, {"git", "gh_path", "lsof"})
    for key in ("git", "lsof"):
        if tools[key] is not None and not _absolute_normalized(tools[key]):
            _fail("TRUST_TOOL_INVALID")
    gh_path = tools["gh_path"]
    if gh_path is not None and (type(gh_path) is not str or
            not 1 <= len(gh_path.split(":")) <= 6 or
            any(not _absolute_normalized(part) for part in gh_path.split(":"))):
        _fail("TRUST_TOOL_INVALID")


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


# BEGIN llmtool-trust-roots v1
def _validate_roots(value, uid, home):
    roots, infos = [], []
    for key in ("canonical_root", "workspace_root", "tasks_root"):
        path = value[key]
        try:
            info = os.stat(path, follow_symlinks=False)
            real = os.path.realpath(path)
        except OSError:
            _fail("TRUST_PATH_INVALID")
        if (not stat.S_ISDIR(info.st_mode) or real != path or info.st_uid != uid or
                stat.S_IMODE(info.st_mode) & 0o022):
            _fail("TRUST_PATH_INVALID")
        roots.append(path)
        infos.append(info)
    canonical, workspace, tasks = roots
    if _overlap(canonical, workspace):
        _fail("TRUST_PATHS_OVERLAP")
    if not _strict_child(tasks, workspace) or stat.S_IMODE(infos[2].st_mode) & 0o022:
        _fail("TRUST_PATHS_OVERLAP")
    config_dir = os.path.join(home, ".config", "llmtool")
    for root in roots:
        if root == home or _overlap(config_dir, root):
            _fail("TRUST_PATHS_OVERLAP")


def _strict_child(path, parent):
    try:
        return os.path.commonpath((path, parent)) == parent and path != parent
    except ValueError:
        return False


def _overlap(left, right):
    return left == right or _strict_child(left, right) or _strict_child(right, left)
# END llmtool-trust-roots v1


def _validate_tools(tools, uid):
    for key in ("git", "lsof"):
        path = tools[key]
        if path is not None and not _safe_tool_file(path, uid):
            _fail("TRUST_TOOL_INVALID")
    if tools["gh_path"] is not None:
        for path in tools["gh_path"].split(":"):
            if not _safe_tool_directory(path, uid):
                _fail("TRUST_TOOL_INVALID")


def _safe_tool_file(path, uid):
    try:
        if os.path.realpath(path) != path:
            return False
        info = os.stat(path, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK) or
                info.st_uid not in (0, uid) or stat.S_IMODE(info.st_mode) & 0o022):
            return False
        return _safe_tool_ancestors(os.path.dirname(path), uid)
    except OSError:
        return False


def _safe_tool_directory(path, uid):
    try:
        if os.path.realpath(path) != path:
            return False
        info = os.stat(path, follow_symlinks=False)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid) or
                stat.S_IMODE(info.st_mode) & 0o022):
            return False
        return _safe_tool_ancestors(os.path.dirname(path), uid)
    except OSError:
        return False


def _safe_tool_ancestors(path, uid):
    current = Path(path)
    while True:
        try:
            info = current.stat()
        except OSError:
            return False
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid) or
                (stat.S_IMODE(info.st_mode) & 0o022 and not
                 (info.st_uid == 0 and (info.st_mode & stat.S_ISVTX)))):
            return False
        if current.parent == current:
            return True
        current = current.parent


def _binding(value):
    identity = {"canonical_root": value["canonical_root"],
                "workspace_root": value["workspace_root"],
                "tasks_root": value["tasks_root"],
                "slug": value["repository"]["slug"],
                "allowed_origins": value["repository"]["allowed_origins"]}
    payload = json.dumps(identity, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def load_trust(home=None, *, _allow_root=False):
    """Load and validate the current owner's trust.json, failing closed."""
    global _TRUST_CACHE
    use_process_cache = home is None
    # Explicit homes are an internal test seam and never affect the process cache.
    if use_process_cache and _TRUST_CACHE is not None:
        return _TRUST_CACHE
    if os.name != "posix" or sys.platform.startswith("win"):
        _fail("TRUST_UNSUPPORTED_PLATFORM")
    if (not hasattr(os, "getuid") or not hasattr(os, "geteuid") or
            os.getuid() != os.geteuid() or (os.geteuid() == 0 and not _allow_root)):
        _fail("TRUST_IDENTITY_INVALID")
    uid = os.geteuid()
    if home is None:
        try:
            home = pwd.getpwuid(uid).pw_dir
        except (KeyError, OSError):
            _fail("TRUST_HOME_INVALID")
    if (not _absolute_normalized(home) or os.path.realpath(home) != home):
        _fail("TRUST_HOME_INVALID")
    try:
        raw = _read_trust_bytes(home, uid)
    except TrustError:
        raise
    if len(raw) > MAX_TRUST_BYTES:
        _fail("TRUST_TOO_LARGE")
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("TRUST_MALFORMED")
    try:
        text = raw.decode("utf-8")
        _check_json_nesting(text)
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError):
        _fail("TRUST_MALFORMED")
    _validate_schema(value)
    _validate_roots(value, uid, home)
    _validate_tools(value["tools"], uid)
    if not _origins_consistent(value):
        _fail("TRUST_SCHEMA_INVALID")
    digest = hashlib.sha256(raw).hexdigest()
    result = TrustConfig(1, value["canonical_root"], value["workspace_root"], value["tasks_root"],
                         _readonly(value["repository"]), _readonly(value["ci_trust"]),
                         _readonly(value["tools"]), digest, _binding(value))
    if use_process_cache:
        _TRUST_CACHE = result
    return result


def _origins_consistent(value):
    slug = value["repository"]["slug"]
    allowed = {f"git@github.com:{slug}.git", f"https://github.com/{slug}.git"}
    return set(value["repository"]["allowed_origins"]) <= allowed


def verify_repository(trust):
    """Verify canonical_root is the configured primary checkout and origin."""
    root = trust.canonical_root
    git_executable = trust.tools["git"] or "/usr/bin/git"
    env = {"PATH": "/usr/bin:/bin", "HOME": os.devnull, "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
           "GIT_PAGER": "cat", "PAGER": "cat"}

    def git(*args):
        try:
            result = subprocess.run([git_executable, "-c", "core.hooksPath=/dev/null", "-C", root, *args],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, env=env, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            _fail("TRUST_REPOSITORY_INVALID")
        if result.returncode:
            _fail("TRUST_REPOSITORY_INVALID")
        return result.stdout.decode("utf-8", "strict").strip()

    try:
        expected_git = os.path.join(root, ".git")
        if git("rev-parse", "--show-toplevel") != root:
            _fail("TRUST_REPOSITORY_INVALID")
        if git("rev-parse", "--path-format=absolute", "--git-common-dir") != expected_git:
            _fail("TRUST_REPOSITORY_INVALID")
        if git("rev-parse", "--absolute-git-dir") != expected_git:
            _fail("TRUST_REPOSITORY_INVALID")
        if git("remote", "get-url", "origin") not in trust.repository["allowed_origins"]:
            _fail("TRUST_REPOSITORY_INVALID")
    except (UnicodeError, ValueError):
        _fail("TRUST_REPOSITORY_INVALID")
    return True
