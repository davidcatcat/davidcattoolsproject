"""Offline read-only Git evidence. No model routing or output-driven commands."""

import json
import os
import re
import selectors
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

from .errors import LLMToolError

DIFF_LIMIT = 2 * 1024 * 1024
METADATA_LIMIT = 1024 * 1024
STDERR_LIMIT = 64 * 1024
GIT_TIMEOUT = 10
LOG_COUNT = 10
RELATED_LIMIT = 20
DIRECTORY_LIMIT = 10
ROOT = ("rev-parse", "--show-toplevel")
HEAD = ("rev-parse", "--verify", "--quiet", "HEAD")
BRANCH = ("branch", "--show-current")
STATUS = ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=all")
DIFF_OPTIONS = ("--no-ext-diff", "--no-textconv", "--no-color", "--ignore-submodules=all",
                "--no-renames", "--src-prefix=a/", "--dst-prefix=b/", "--unified=3")
DIFF = ("diff",) + DIFF_OPTIONS
STAGED_DIFF = ("diff", "--cached") + DIFF_OPTIONS
LOG = ("log", "--max-count=10", "--format=%H%x00%s", "-z", "--no-decorate",
       "--no-show-signature", "--no-notes")
FILTERS = ("config", "--null", "--name-only", "--get-regexp",
           r"^filter\..*\.(clean|smudge|process|required)$")
PROMISOR = ("config", "--null", "--name-only", "--get-regexp",
            r"^(extensions\.partialclone|remote\..*\.promisor)$")
ALLOWED = frozenset((ROOT, HEAD, BRANCH, STATUS, DIFF, STAGED_DIFF, LOG, FILTERS, PROMISOR))
SAFE_CONFIG = (
    "core.fsmonitor=false", "core.hooksPath=/dev/null", "core.untrackedCache=false",
    "core.attributesFile=/dev/null", "core.pager=cat", "color.ui=false",
    "status.renames=true", "diff.submodule=short", "diff.algorithm=myers",
    "diff.indentHeuristic=false", "diff.orderFile=/dev/null", "log.showSignature=false",
    "protocol.allow=never", "protocol.file.allow=never", "protocol.http.allow=never",
    "protocol.https.allow=never", "protocol.ssh.allow=never", "protocol.git.allow=never",
    "protocol.ext.allow=never", "maintenance.auto=false", "gc.auto=0",
)


def _environment():
    # Never inherit GIT_DIR/WORK_TREE/CONFIG, credential, SSH or loader injection.
    return {
        "PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat", "PAGER": "cat", "GIT_LITERAL_PATHSPECS": "1",
    }


def _path(text):
    if (not text or text.startswith("/") or "\x00" in text
            or any(part in (".", "..") for part in text.split("/"))):
        raise LLMToolError("Git returned an unsafe repository path")
    return text


def _decode(raw):
    return raw.decode("utf-8", "surrogateescape")


class _GitRunner:
    def __init__(self, cwd):
        self.cwd = Path(cwd).resolve()
        self._filter_config = ()

    def run(self, args):
        args = tuple(args)
        tree = (len(args) == 3 and args[:2] == ("ls-tree", "-z")
                and (args[2] == "HEAD" or args[2].startswith("HEAD:")))
        if tree and args[2] != "HEAD":
            _path(args[2][5:])
        tree_path = (len(args) == 5 and args[:4] == ("ls-tree", "-z", "HEAD", "--"))
        if tree_path:
            _path(args[4])
        if args not in ALLOWED and not tree and not tree_path:
            raise LLMToolError("Git operation is not allowed")
        limit = DIFF_LIMIT if args in (DIFF, STAGED_DIFF) else METADATA_LIMIT
        argv = ["git", "--no-pager"]
        for config in SAFE_CONFIG + self._filter_config:
            argv.extend(("-c", config))
        argv.extend(args)
        try:
            process = subprocess.Popen(argv, cwd=self.cwd, env=_environment(),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, shell=False,
                                       close_fds=True, start_new_session=True)
        except OSError:
            raise LLMToolError("Git could not be started; details omitted") from None
        selector = selectors.DefaultSelector()
        output = bytearray()
        stderr_count = 0
        deadline = time.monotonic() + GIT_TIMEOUT
        try:
            for stream, kind in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, kind)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMToolError("Git inspection timed out")
                for key, _ in selector.select(min(remaining, 0.1)):
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        if len(output) + len(chunk) > limit:
                            message = "Git diff exceeds 2 MiB limit" if args in (DIFF, STAGED_DIFF) else "Git metadata exceeds 1 MiB limit"
                            raise LLMToolError(message)
                        output.extend(chunk)
                    else:
                        stderr_count += len(chunk)
                        if stderr_count > STDERR_LIMIT:
                            raise LLMToolError("Git diagnostics exceed limit; details omitted")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMToolError("Git inspection timed out")
            try:
                code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise LLMToolError("Git inspection timed out") from None
        except OSError:
            raise LLMToolError("Git inspection I/O failed; details omitted") from None
        finally:
            selector.close()
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    # macOS may deny signalling an already-exiting group.
                    # Popen.kill polls first and targets only our child.
                    process.kill()
                process.wait()
            process.stdout.close()
            process.stderr.close()
        if code != 0 and not (code == 1 and args in (HEAD, FILTERS, PROMISOR)):
            raise LLMToolError("Git inspection failed; expected an accessible local repository")
        return bytes(output)

    def secure_repository(self):
        if self.run(PROMISOR):
            raise LLMToolError("partial/promisor repositories are unsupported for offline inspection")
        keys = self.run(FILTERS).split(b"\x00")
        overrides = []
        for raw in keys:
            if not raw:
                continue
            key = _decode(raw)
            if not re.fullmatch(r"filter\.[^\x00-\x20\x7f=]+\.(clean|smudge|process|required)", key):
                raise LLMToolError("unsupported Git filter configuration")
            overrides.append(key + ("=false" if key.endswith(".required") else "="))
        self._filter_config = tuple(sorted(set(overrides)))


@dataclass(frozen=True)
class GitRepositoryInfo:
    root: str
    branch: Optional[str]
    head: Optional[str]
    detached: bool


@dataclass(frozen=True)
class GitChange:
    path: str
    index: str
    working_tree: str
    original_path: Optional[str] = None


@dataclass(frozen=True)
class GitStatus:
    staged: tuple[str, ...]
    unstaged: tuple[str, ...]
    untracked: tuple[str, ...]
    conflicted: tuple[str, ...]
    entries: tuple[GitChange, ...]

    def __post_init__(self):
        for name in ("staged", "unstaged", "untracked", "conflicted", "entries"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class GitDiff:
    text: str
    truncated: bool
    byte_count: int


@dataclass(frozen=True)
class GitCommit:
    sha: str
    subject: str


@dataclass(frozen=True)
class GitSnapshot:
    repository: GitRepositoryInfo
    status: GitStatus
    changed_files: tuple[str, ...]
    staged_files: tuple[str, ...]
    recent_commits: tuple[GitCommit, ...]
    diff: GitDiff
    staged_diff: GitDiff
    related_files: tuple[str, ...]

    def __post_init__(self):
        for name in ("changed_files", "staged_files", "recent_commits", "related_files"):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    def to_dict(self):
        # JSON round trip converts tuple containers into public arrays.
        return json.loads(json.dumps(asdict(self), ensure_ascii=True))

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=True, indent=2)


def parse_status(raw):
    if raw and not raw.endswith(b"\x00"):
        raise LLMToolError("Git status is malformed")
    records = raw.split(b"\x00")[:-1]
    staged, unstaged, untracked, conflicted = set(), set(), set(), set()
    entries = []
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if len(record) < 4 or record[2:3] != b" ":
            raise LLMToolError("Git status is malformed")
        xy = _decode(record[:2])
        if xy not in ("??", "!!") and any(c not in " MADRCUT" for c in xy):
            raise LLMToolError("Git status is malformed")
        path = _path(_decode(record[3:]))
        original = None
        if "R" in xy or "C" in xy:
            if i >= len(records):
                raise LLMToolError("Git rename status is malformed")
            original = _path(_decode(records[i]))
            i += 1
        entries.append(GitChange(path, xy[0], xy[1], original))
        if xy == "??":
            untracked.add(path)
        elif xy in ("DD", "AU", "UD", "UA", "DU", "AA", "UU"):
            conflicted.add(path)
        elif xy != "!!":
            if xy[0] != " ":
                staged.add(path)
            if xy[1] != " ":
                unstaged.add(path)
    return GitStatus(*(tuple(sorted(values)) for values in (staged, unstaged, untracked, conflicted)),
                     tuple(sorted(entries, key=lambda item: (item.path, item.index, item.working_tree))))


def parse_log(raw):
    if not raw:
        return ()
    if not raw.endswith(b"\x00"):
        raise LLMToolError("Git log is malformed")
    records = raw.split(b"\x00")[:-1]
    if len(records) % 2 or len(records) // 2 > LOG_COUNT:
        raise LLMToolError("Git log is malformed")
    commits = []
    for i in range(0, len(records), 2):
        sha = _decode(records[i])
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
            raise LLMToolError("Git log is malformed")
        commits.append(GitCommit(sha, _decode(records[i + 1])))
    return tuple(commits)


def _related_files(runner, changed):
    directories = sorted({str(PurePosixPath(path).parent) for path in changed})[:DIRECTORY_LIMIT]
    related = set()
    for directory in directories:
        tree = "HEAD" if directory == "." else "HEAD:" + _path(directory)
        if directory != ".":
            # Ask Git for the literal directory entry first. New directories
            # are absent from HEAD, rather than failures to inspect a tree.
            entry = runner.run(("ls-tree", "-z", "HEAD", "--", directory))
            if not entry or not any(kind == b"tree" for kind, _ in _tree_entries(entry)):
                continue
        raw = runner.run(("ls-tree", "-z", tree))
        for kind, filename in _tree_entries(raw):
            if kind != b"blob":
                continue
            path = filename if directory == "." else directory + "/" + filename
            if path not in changed:
                related.add(path)
    return tuple(sorted(related)[:RELATED_LIMIT])


def _tree_entries(raw):
    if raw and not raw.endswith(b"\x00"):
        raise LLMToolError("Git tree metadata is malformed")
    for record in raw.split(b"\x00")[:-1]:
        try:
            metadata, filename = record.split(b"\t", 1)
            mode, kind, sha = metadata.split(b" ")
        except ValueError:
            raise LLMToolError("Git tree metadata is malformed") from None
        if kind not in (b"blob", b"tree", b"commit") or not re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", sha):
            raise LLMToolError("Git tree metadata is malformed")
        yield kind, _path(_decode(filename))


def collect_git_snapshot(cwd=None):
    runner = _GitRunner(Path.cwd() if cwd is None else cwd)
    root = _decode(runner.run(ROOT).removesuffix(b"\n"))
    if not root or not os.path.isabs(root):
        raise LLMToolError("Git repository root is malformed")
    runner.cwd = Path(root)
    runner.secure_repository()
    branch = _decode(runner.run(BRANCH).removesuffix(b"\n")) or None
    head = _decode(runner.run(HEAD).removesuffix(b"\n")) or None
    if head is not None and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
        raise LLMToolError("Git HEAD is malformed")
    status = parse_status(runner.run(STATUS))
    changed = tuple(sorted(set(status.staged + status.unstaged + status.untracked + status.conflicted)))
    diff = runner.run(DIFF)
    staged_diff = runner.run(STAGED_DIFF)
    return GitSnapshot(GitRepositoryInfo(root, branch, head, branch is None and head is not None),
                       status, changed, status.staged, parse_log(runner.run(LOG)) if head else (),
                       GitDiff(_decode(diff), False, len(diff)), GitDiff(_decode(staged_diff), False, len(staged_diff)),
                       _related_files(runner, changed) if head else ())


def format_git_snapshot(snapshot):
    quote = lambda value: json.dumps(value, ensure_ascii=True)
    lines = ["Repository: " + quote(snapshot.repository.root),
             "Branch: " + quote(snapshot.repository.branch),
             "HEAD: " + (snapshot.repository.head or "unborn"),
             "Detached: " + ("yes" if snapshot.repository.detached else "no")]
    for label in ("staged", "unstaged", "untracked", "conflicted"):
        paths = getattr(snapshot.status, label)
        lines.append(label.upper() + ": " + (", ".join(quote(path) for path in paths) or "(none)"))
    lines.append("RELATED: " + (", ".join(quote(path) for path in snapshot.related_files) or "(none)"))
    lines.append("RECENT COMMITS:")
    lines.extend(commit.sha + " " + quote(commit.subject) for commit in snapshot.recent_commits)
    for label, diff in (("UNSTAGED DIFF", snapshot.diff), ("STAGED DIFF", snapshot.staged_diff)):
        lines.append(label + " (" + str(diff.byte_count) + " bytes, complete):")
        # Escape terminal controls while preserving the exact text in JSON/API.
        safe = "".join(c if c.isprintable() or c in "\n\t" else quote(c)[1:-1] for c in diff.text)
        lines.append(safe or "(empty)")
    return "\n".join(lines)
