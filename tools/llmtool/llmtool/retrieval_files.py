"""A10 explicit-scope metadata discovery and bounded, read-only evidence access."""

import hashlib
import json
import os
import re
import stat
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import LLMToolError
from .terminal import contains_unsafe_controls

MAX_MANIFEST_FILES = 4096
MAX_MANIFEST_BYTES = 256 * 1024
MAX_SELECTED_FILES = 8
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_EVIDENCE_BYTES = 512 * 1024
MAX_PATH_BYTES = 4096
MAX_SCOPES = 32
MAX_PATH_DEPTH = 64
MAX_SCAN_ENTRIES = 16384


def relative_path(value, allow_dot=False):
    if not isinstance(value, str) or not value:
        raise LLMToolError("retrieval requires nonempty relative paths")
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        raise LLMToolError("retrieval paths must be UTF-8") from None
    if (len(raw) > MAX_PATH_BYTES or contains_unsafe_controls(value)
            or any(ord(c) < 32 for c in value)
            or any(unicodedata.category(c) == "Cf" for c in value)
            or "\\" in value or re.match(r"^[A-Za-z]:", value)):
        raise LLMToolError("retrieval path syntax is unsupported")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or len(path.parts) > MAX_PATH_DEPTH:
        raise LLMToolError("retrieval paths cannot be absolute or traverse parents")
    normalized = path.as_posix()
    if normalized == "." and not allow_dot:
        raise LLMToolError("retrieval requires a file path")
    return normalized


def excluded_credential_path(value):
    """Narrow file/store exclusions, not A6's broad content-display policy."""
    parts = PurePosixPath(value).parts
    lowered = tuple(p.lower() for p in parts)
    if any(p in (".ssh", ".password-store", ".aws", ".kube", ".docker", ".gnupg", ".azure")
           for p in lowered):
        return True
    if any(lowered[i:i + 2] == (".config", "llmtool") for i in range(len(lowered) - 1)):
        return True
    name = lowered[-1] if lowered else ""
    return (name in (".env", ".netrc", ".npmrc", ".pypirc", ".pgpass", ".git-credentials",
                     ".vault-token", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
            or name.startswith(".env.") or name.endswith((".pem", ".key", ".p12", ".pfx"))
            or lowered[-2:] in (("etc", "shadow"), ("etc", "gshadow"))
            or (len(lowered) >= 3 and lowered[-3] == "proc" and lowered[-1] == "environ"
                and (lowered[-2] in ("self", "thread-self") or lowered[-2].isascii() and lowered[-2].isdigit())))


def _excluded(path):
    return any(p.lower() == ".git" for p in PurePosixPath(path).parts) or excluded_credential_path(path)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _directory(parent, name):
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISDIR(before.st_mode):
        raise LLMToolError("retrieval directory must be real and non-symlink")
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        after = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if not stat.S_ISDIR(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(fd)
        raise LLMToolError("retrieval directory changed during access")
    return fd


@contextmanager
def _parent(root_fd, path):
    parts = PurePosixPath(path).parts
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = _directory(fd, part)
            os.close(fd)
            fd = child
        yield fd, parts[-1] if parts else "."
    finally:
        os.close(fd)


@dataclass(frozen=True)
class AuthorizedRoot:
    descriptor: int
    scopes: tuple
    credential_prefix: str


@contextmanager
def authorize_root(root, scopes):
    """Hold an anchored root descriptor throughout both calls; never use HOME."""
    if (os.name != "posix" or not hasattr(os, "O_NOFOLLOW")
            or os.open not in os.supports_dir_fd or os.scandir not in os.supports_fd):
        raise LLMToolError("retrieval requires POSIX no-follow directory access")
    if not isinstance(scopes, (list, tuple)) or not 1 <= len(scopes) <= MAX_SCOPES:
        raise LLMToolError("retrieval requires 1..32 explicit scopes")
    normalized = sorted(set(relative_path(s, allow_dot=True) for s in scopes))
    try:
        root_text = os.fspath(root)
        if not root_text:
            raise LLMToolError("retrieval root must be explicit and nonempty")
        # Root may be absolute, but reject ambiguous parent traversal/controls.
        relative_path(root_text.lstrip("/") or ".", allow_dot=True)
        absolute = Path(os.path.abspath(root_text))
        if _excluded(absolute.as_posix()):
            raise LLMToolError("retrieval root is inside an excluded credential/Git location")
        fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in absolute.parts[1:]:
                child = _directory(fd, part)
                os.close(fd)
                fd = child
            for scope in normalized:
                if _excluded(scope) or excluded_credential_path((absolute / scope).as_posix()):
                    raise LLMToolError("retrieval scope is excluded by the credential/Git policy")
                with _parent(fd, scope) as (directory, name):
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                        raise LLMToolError("retrieval scope must be a real regular file or directory")
            minimal = []
            for scope in normalized:
                if not any(p == "." or scope.startswith(p + "/") for p in minimal):
                    minimal.append(scope)
            yield AuthorizedRoot(fd, tuple(minimal), absolute.as_posix())
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError):
        raise LLMToolError("retrieval root/scope access failed; details omitted") from None


@dataclass(frozen=True)
class CandidateFile:
    path: str
    byte_count: int
    identity: tuple

    def __post_init__(self):
        if (relative_path(self.path) != self.path or _excluded(self.path)
                or type(self.byte_count) is not int or self.byte_count < 0
                or not isinstance(self.identity, tuple) or len(self.identity) != 5
                or any(type(n) is not int for n in self.identity)):
            raise LLMToolError("retrieval candidate metadata is invalid")

    def to_dict(self):
        return {"path": self.path, "byte_count": self.byte_count}


def manifest_json(manifest):
    return json.dumps([item.to_dict() for item in manifest], ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def build_manifest(root):
    """Metadata only. Bounds fail the whole scan; never silently truncate."""
    files = {}
    scanned = 0
    serialized_bytes = 2

    def add(path, info):
        nonlocal serialized_bytes
        if path in files:
            return
        if len(files) >= MAX_MANIFEST_FILES:
            raise LLMToolError("retrieval manifest file limit exceeded; narrow --scope")
        item = CandidateFile(path, info.st_size, _identity(info))
        serialized_bytes += len(manifest_json((item,)).encode("utf-8")) - 2 + bool(files)
        if serialized_bytes > MAX_MANIFEST_BYTES:
            raise LLMToolError("retrieval manifest byte limit exceeded; narrow --scope")
        files[path] = item

    def walk(directory, prefix):
        nonlocal scanned
        names = []
        with os.scandir(directory) as entries:
            for entry in entries:
                scanned += 1
                if scanned > MAX_SCAN_ENTRIES:
                    raise LLMToolError("retrieval scan entry limit exceeded; narrow --scope")
                path = relative_path(prefix + "/" + entry.name if prefix else entry.name)
                if not _excluded(path) and not excluded_credential_path(root.credential_prefix + "/" + path):
                    names.append((entry.name, path))
        for name, path in sorted(names):
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = _directory(directory, name)
                try:
                    walk(child, path)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                add(path, info)
            # Symlinks, devices, sockets and FIFOs never become candidates.

    try:
        for scope in root.scopes:
            with _parent(root.descriptor, scope) as (directory, name):
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = _directory(directory, name)
                    try:
                        walk(child, "" if scope == "." else scope)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    add(scope, info)
                else:
                    raise LLMToolError("retrieval scope changed before manifest construction")
    except OSError:
        raise LLMToolError("retrieval manifest access failed; details omitted") from None
    return tuple(files[path] for path in sorted(files))


@dataclass(frozen=True)
class RetrievedEvidence:
    path: str
    content: str
    byte_count: int
    sha256: str

    def __post_init__(self):
        if relative_path(self.path) != self.path or _excluded(self.path):
            raise LLMToolError("retrieval evidence path is invalid")
        try:
            raw = self.content.encode("utf-8")
        except (AttributeError, UnicodeError):
            raise LLMToolError("retrieval evidence must be UTF-8 text") from None
        if contains_unsafe_controls(self.content):
            raise LLMToolError("retrieval evidence contains unsafe text controls")
        if (type(self.byte_count) is not int or self.byte_count != len(raw)
                or len(raw) > MAX_FILE_BYTES or self.sha256 != hashlib.sha256(raw).hexdigest()):
            raise LLMToolError("retrieval evidence byte count/hash is invalid")

    def metadata(self):
        return {"path": self.path, "byte_count": self.byte_count, "sha256": self.sha256}


def read_selected(root, manifest, selected):
    """Read only fully validated manifest entries, with before/open/after checks."""
    lookup = {item.path: item for item in manifest}
    if (not isinstance(selected, (list, tuple)) or len(selected) > MAX_SELECTED_FILES
            or any(type(p) is not str for p in selected)
            or len(set(selected)) != len(selected) or any(p not in lookup for p in selected)):
        raise LLMToolError("retrieval selection is outside the manifest")
    records, total = [], 0
    if any(lookup[p].byte_count > MAX_FILE_BYTES for p in selected):
        raise LLMToolError("retrieval selected file exceeds 256 KiB")
    if sum(lookup[p].byte_count for p in selected) > MAX_TOTAL_EVIDENCE_BYTES:
        raise LLMToolError("retrieval evidence exceeds 512 KiB")
    for path in selected:
        expected = lookup[path]
        try:
            with _parent(root.descriptor, path) as (directory, name):
                before = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(before.st_mode) or _identity(before) != expected.identity:
                    raise LLMToolError("retrieval selected file changed since manifest")
                if before.st_size > MAX_FILE_BYTES:
                    raise LLMToolError("retrieval selected file exceeds 256 KiB")
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
                        raise LLMToolError("retrieval selected file changed during open")
                    raw = stream.read(MAX_FILE_BYTES + 1)
                    after = os.fstat(stream.fileno())
                    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if _identity(after) != _identity(opened) or _identity(current) != _identity(opened):
                        raise LLMToolError("retrieval selected file changed during read")
        except OSError:
            raise LLMToolError("retrieval selected file access failed; details omitted") from None
        if len(raw) > MAX_FILE_BYTES:
            raise LLMToolError("retrieval selected file exceeds 256 KiB")
        if len(raw) != expected.byte_count:
            raise LLMToolError("retrieval selected file byte count changed")
        total += len(raw)
        if total > MAX_TOTAL_EVIDENCE_BYTES:
            raise LLMToolError("retrieval evidence exceeds 512 KiB")
        try:
            content = raw.decode("utf-8")
        except UnicodeError:
            raise LLMToolError("retrieval selected file must be UTF-8 text") from None
        records.append(RetrievedEvidence(path, content, len(raw), hashlib.sha256(raw).hexdigest()))
    return tuple(records)
