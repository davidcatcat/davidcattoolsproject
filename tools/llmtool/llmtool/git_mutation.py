"""Internal ADD preflight only. No execution, authorization or public CLI wiring.

Git is a trusted executable selected by PATH, as in A5. Repository content is
untrusted. This is a bounded snapshot, not isolation against malicious same-UID
concurrency. A later mutation slice must separately close its containment gate.
"""
import hashlib
import os
import re
import selectors
import signal
import stat
import struct
import subprocess
import time
import unicodedata
from contextlib import ExitStack
from dataclasses import dataclass
from enum import Enum

from . import action_contract as contract
from .errors import LLMToolError

METADATA_LIMIT = 1024 * 1024
INDEX_LIMIT = 2 * METADATA_LIMIT
ENTRY_LIMIT = 20000
STDERR_LIMIT = 65536
GIT_TIMEOUT = 10
SAFE_CONFIG = (
    'core.fsmonitor=false', 'core.hooksPath=/dev/null', 'core.untrackedCache=false',
    'core.attributesFile=/dev/null', 'core.excludesFile=/dev/null',
    'core.pager=cat', 'color.ui=false',
    'status.renames=false', 'core.autocrlf=false', 'core.safecrlf=false',
    'add.ignoreErrors=false', 'diff.external=', 'diff.submodule=short',
    'diff.algorithm=myers', 'diff.indentHeuristic=false', 'diff.orderFile=/dev/null',
    'log.showSignature=false', 'credential.helper=', 'core.sshCommand=',
    'gpg.program=', 'commit.gpgSign=false', 'tag.gpgSign=false',
    'protocol.allow=never', 'protocol.file.allow=never', 'protocol.http.allow=never',
    'protocol.https.allow=never', 'protocol.ssh.allow=never', 'protocol.git.allow=never',
    'protocol.ext.allow=never', 'maintenance.auto=false', 'gc.auto=0',
)
ATTRIBUTES = ('filter', 'text', 'eol', 'crlf', 'ident', 'working-tree-encoding')
_CATEGORIES = frozenset(('action mismatch', 'repository identity mismatch',
    'unsupported repo layout', 'HEAD mismatch', 'index mismatch',
    'file/path mismatch', 'unsafe filter', 'unsafe attribute', 'unsafe config',
    'unsupported index state', 'conflict', 'Git inspection unavailable'))


class GitPreflightError(LLMToolError):
    """Fixed sanitized category; no Git output, paths, config values or content."""
    def __init__(self, category):
        self.category = category if category in _CATEGORIES else 'Git inspection unavailable'
        super().__init__('Git ADD preflight rejected: ' + self.category)


def _reject(category):
    raise GitPreflightError(category)


@dataclass(frozen=True)
class IndexEntry:
    path: bytes
    mode: int
    oid: str
    stage: int
    skip_worktree: bool
    intent_to_add: bool
    assume_unchanged: bool


@dataclass(frozen=True)
class BoundBlob:
    path: str
    oid: str
    mode: int


@dataclass(frozen=True)
class GitAddPreflight:
    action_digest: str
    repository_root: str
    root_device: int
    root_inode: int
    head: str
    object_format: str
    index_sha256: str
    paths: tuple[str, ...]
    state_digest: str
    semantic_index: tuple[IndexEntry, ...]
    bound_blobs: tuple[BoundBlob, ...]
    future_add_argv: tuple[str, ...]
    future_environment: tuple[tuple[str, str], ...]


def git_blob_oid(content, object_format):
    """Git blob identity of exact bound bytes; no subprocess or object write."""
    if type(content) is not bytes or len(content) > contract.MAX_CONTENT_BYTES:
        _reject('file/path mismatch')
    if object_format not in ('sha1', 'sha256'):
        _reject('unsupported repo layout')
    return hashlib.new(object_format, b'blob ' + str(len(content)).encode('ascii')
                       + b'\0' + content).hexdigest()


def _fingerprint(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid,
            st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _private(st, directory, category):
    if (not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode))
            or st.st_uid != os.geteuid() or st.st_mode & 0o022
            or (not directory and st.st_nlink != 1)):
        _reject(category)


def _open_dir(stack, name, parent=None, private=True, category='file/path mismatch'):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    stack.callback(os.close, fd)
    if private:
        _private(os.fstat(fd), True, category)
    return fd


def _root(stack, binding):
    if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW') or os.geteuid() == 0:
        _reject('repository identity mismatch')
    path = binding.canonical_root
    if path != os.path.realpath(path):
        _reject('repository identity mismatch')
    # Check every component without following links, not only the final root.
    fd = _open_dir(stack, '/', private=False)
    for part in path.split('/')[1:]:
        fd = _open_dir(stack, part, fd, private=False)
    st = os.fstat(fd)
    _private(st, True, 'repository identity mismatch')
    if (st.st_dev, st.st_ino) != (binding.root_device, binding.root_inode):
        _reject('repository identity mismatch')
    return fd


def _read_file(parent, name, limit, category):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = os.fstat(fd)
        _private(before, False, category)
        if before.st_size > limit:
            _reject(category)
        raw = bytearray()
        while len(raw) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (len(raw) > limit or _fingerprint(before) != _fingerprint(after)
                or _fingerprint(after) != _fingerprint(named)):
            _reject(category)
        return bytes(raw), after
    finally:
        os.close(fd)


def _index_digest(raw):
    return hashlib.sha256(raw).hexdigest()


def hash_git_index(repository):
    """One raw-index SHA-256 algorithm for preflight and future Action builders.

    Accepts only an A11b.1 RepositoryBinding, never an arbitrary index path.
    This helper does not authorize execution or validate the rest of a repo.
    """
    if type(repository) is not contract.RepositoryBinding:
        _reject('action mismatch')
    try:
        with ExitStack() as stack:
            root = _root(stack, repository)
            git = _open_dir(stack, '.git', root, category='unsupported repo layout')
            if 'index.lock' in os.listdir(git):
                _reject('index mismatch')
            raw, _ = _read_file(git, 'index', INDEX_LIMIT, 'index mismatch')
            return _index_digest(raw)
    except OSError:
        raise GitPreflightError('index mismatch') from None


def _parse_index(raw, object_format):
    """Bounded native index v2/v3/v4 parsing, including extended entry flags.

    Git's binary cache flags are needed: ls-files -s alone hides intent-to-add.
    Optional uppercase cache extensions are skipped; mandatory unknown ones fail.
    """
    try:
        oid_size = {'sha1': 20, 'sha256': 32}[object_format]
        end = len(raw) - oid_size
        if (len(raw) > INDEX_LIMIT or end < 12 or raw[:4] != b'DIRC'
                or hashlib.new(object_format, raw[:end]).digest() != raw[end:]):
            _reject('unsupported index state')
        version, count = struct.unpack_from('>II', raw, 4)
        if version not in (2, 3, 4) or count > ENTRY_LIMIT:
            _reject('unsupported index state')
        cursor, previous, entries = 12, b'', []
        for _ in range(count):
            start = cursor
            if cursor + 42 + oid_size > end:
                _reject('unsupported index state')
            mode = struct.unpack_from('>I', raw, cursor + 24)[0]
            oid = raw[cursor+40:cursor+40+oid_size].hex()
            flags = struct.unpack_from('>H', raw, cursor + 40 + oid_size)[0]
            cursor += 42 + oid_size
            extended = 0
            if flags & 0x4000:
                if version == 2:
                    _reject('unsupported index state')
                extended = struct.unpack_from('>H', raw, cursor)[0]
                cursor += 2
                if extended & ~0x6000:
                    _reject('unsupported index state')
            if version == 4:
                byte = raw[cursor]; cursor += 1
                remove = byte & 0x7f
                while byte & 0x80:
                    byte = raw[cursor]; cursor += 1
                    remove = ((remove + 1) << 7) | (byte & 0x7f)
                    if remove > len(previous):
                        _reject('unsupported index state')
                if remove > len(previous):
                    _reject('unsupported index state')
                stop = raw.index(b'\0', cursor, end)
                path = previous[:len(previous)-remove] + raw[cursor:stop]
                cursor = stop + 1
            else:
                stop = raw.index(b'\0', cursor, end)
                path = raw[cursor:stop]
                cursor = start + ((stop + 1 - start + 7) // 8) * 8
                if any(raw[stop:cursor]):
                    _reject('unsupported index state')
            if (not path or path.startswith(b'/') or len(path) > 4096
                    or any(p in (b'', b'.', b'..', b'.git') for p in path.split(b'/'))
                    or mode not in (0o100644, 0o100755, 0o120000, 0o160000)
                    or (flags & 0xfff) != min(len(path), 0xfff) or cursor > end):
                _reject('unsupported index state')
            entry = IndexEntry(path, mode, oid, (flags >> 12) & 3,
                bool(extended & 0x4000), bool(extended & 0x2000), bool(flags & 0x8000))
            if entries and (entries[-1].path, entries[-1].stage) >= (path, entry.stage):
                _reject('unsupported index state')
            entries.append(entry); previous = path
        while cursor < end:
            signature, size = struct.unpack_from('>4sI', raw, cursor)
            cursor += 8
            if (cursor + size > end or signature in (b'link', b'sdir')
                    or not 65 <= signature[0] <= 90):
                _reject('unsupported index state')
            cursor += size
        if cursor != end:
            _reject('unsupported index state')
        return tuple(entries)
    except (KeyError, ValueError, IndexError, struct.error):
        raise GitPreflightError('unsupported index state') from None


def _environment(root, readonly):
    env = {'PATH': os.environ.get('PATH', os.defpath), 'LC_ALL': 'C',
        'GIT_DIR': root+'/.git', 'GIT_WORK_TREE': root,
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
        'GIT_ATTR_NOSYSTEM': '1', 'GIT_NO_LAZY_FETCH': '1',
        'GIT_TERMINAL_PROMPT': '0', 'GIT_LITERAL_PATHSPECS': '1',
        'GIT_PAGER': 'cat', 'PAGER': 'cat'}
    if readonly:
        env['GIT_OPTIONAL_LOCKS'] = '0'
    return env


def _prefix():
    return ('git', '--no-pager') + tuple(x for setting in SAFE_CONFIG for x in ('-c', setting))


class _Read(Enum):
    CONFIG = 1
    FORMAT = 2
    HEAD = 3
    BRANCH = 4
    TREE = 5
    ATTRIBUTES = 6
    IGNORE = 7


class _Reads:
    """Private fixed read queries. No arbitrary argv, env or subcommand seam."""
    def __init__(self, action):
        self.root = action.repository.canonical_root
        self.paths = tuple(f.path for f in action.payload.files)

    def query(self, query):
        commands = {
            _Read.CONFIG: ('config', '--file', self.root+'/.git/config', '--no-includes', '--null', '--list'),
            _Read.FORMAT: ('rev-parse', '--show-object-format'),
            _Read.HEAD: ('rev-parse', '--verify', 'HEAD^{commit}'),
            _Read.BRANCH: ('symbolic-ref', '--quiet', 'HEAD'),
            _Read.TREE: ('ls-tree', '-r', '-z', '--full-tree', 'HEAD'),
            _Read.ATTRIBUTES: ('check-attr', '-z') + ATTRIBUTES + ('--',) + self.paths,
            _Read.IGNORE: ('ls-files', '--others', '--ignored', '--exclude-standard', '-z', '--') + self.paths,
        }
        if type(query) is not _Read:
            _reject('Git inspection unavailable')
        try:
            process = subprocess.Popen(_prefix() + commands[query], cwd=self.root,
                env=_environment(self.root, True), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                close_fds=True, start_new_session=True)
        except OSError:
            raise GitPreflightError('Git inspection unavailable') from None
        output, stderr_count = bytearray(), 0
        selector = selectors.DefaultSelector()
        deadline = time.monotonic() + GIT_TIMEOUT
        try:
            for stream, kind in ((process.stdout, 'out'), (process.stderr, 'err')):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, kind)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    _reject('Git inspection unavailable')
                for key, _ in selector.select(0.05):
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.data == 'out':
                        output.extend(chunk)
                        if len(output) > METADATA_LIMIT:
                            _reject('Git inspection unavailable')
                    else:
                        stderr_count += len(chunk)
                        if stderr_count > STDERR_LIMIT:
                            _reject('Git inspection unavailable')
            code = process.wait(timeout=max(0.001, deadline-time.monotonic()))
            if code != 0:
                _reject('HEAD mismatch' if query in (_Read.HEAD, _Read.BRANCH) else 'Git inspection unavailable')
            return bytes(output)
        except (OSError, subprocess.TimeoutExpired):
            raise GitPreflightError('Git inspection unavailable') from None
        finally:
            selector.close()
            # Read-only slice, A5 process-group cleanup. Mutation-specific B5
            # (including host TERM/HUP/crash) remains a separate A11b.2.2 gate.
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                finally:
                    process.wait()
            process.stdout.close()
            process.stderr.close()


def _metadata_snapshot(gitfd):
    """No-follow metadata traversal. Reject alternate layouts before Git setup."""
    result = []
    def walk(fd, prefix):
        names = sorted(os.listdir(fd))
        for name in names:
            path = prefix+name
            if (path in ('commondir', 'objects/info/alternates', 'info/sparse-checkout', 'worktrees')
                    or (not prefix and name.startswith('sharedindex.'))
                    or (prefix == 'objects/pack/' and name.endswith('.promisor'))):
                _reject('unsupported repo layout')
            if path == 'index.lock':
                _reject('index mismatch')
            if path in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'BISECT_LOG',
                        'BISECT_START', 'rebase-apply', 'rebase-merge', 'sequencer'):
                _reject('HEAD mismatch')
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            isdir = stat.S_ISDIR(st.st_mode)
            _private(st, isdir, 'unsupported repo layout')
            result.append((path, _fingerprint(st)))
            if len(result) > ENTRY_LIMIT or len(prefix) > 4096 or prefix.count('/') > 64:
                _reject('unsupported repo layout')
            if isdir:
                with ExitStack() as stack:
                    child = _open_dir(stack, name, fd, category='unsupported repo layout')
                    walk(child, path+'/')
    walk(gitfd, '')
    return tuple(result)


def _check_config(raw):
    if raw and not raw.endswith(b'\0'):
        _reject('unsafe config')
    for record in raw.split(b'\0'):
        if not record:
            continue
        key, _, value = record.partition(b'\n')
        key = key.lower()
        if re.fullmatch(rb'filter\..*\.(clean|smudge|process|required)', key):
            _reject('unsafe filter')
        if (key in (b'include.path', b'core.worktree', b'core.excludesfile')
                or key.startswith(b'includeif.')
                or (key.startswith(b'extensions.') and key != b'extensions.objectformat')
                or re.fullmatch(rb'remote\..*\.promisor', key)):
            _reject('unsafe config')
        # Presence is deliberately conservative for unsupported index modes.
        if key in (b'core.splitindex', b'index.sparse', b'core.sparsecheckout', b'core.sparsecheckoutcone'):
            _reject('unsupported index state')
        if key == b'core.bare' and value.lower() not in (b'false', b'no', b'off', b'0'):
            _reject('unsupported repo layout')
        if key == b'core.filemode' and value.lower() not in (b'true', b'yes', b'on', b'1'):
            _reject('unsafe config')


def _target(rootfd, expectation, object_format):
    with ExitStack() as stack:
        parent = rootfd
        components = expectation.path.split('/')
        for part in components[:-1]:
            if os.fsencode(part) not in tuple(os.fsencode(n) for n in os.listdir(parent)):
                _reject('file/path mismatch')
            parent = _open_dir(stack, part, parent)
            if '.git' in os.listdir(parent):
                _reject('file/path mismatch')
        name = components[-1]
        if os.fsencode(name) not in tuple(os.fsencode(n) for n in os.listdir(parent)):
            _reject('file/path mismatch')
        raw, st = _read_file(parent, name, contract.MAX_CONTENT_BYTES, 'file/path mismatch')
        md = expectation.state.metadata
        actual = (st.st_dev, st.st_ino, stat.S_IMODE(st.st_mode), st.st_uid,
                  st.st_gid, st.st_size, st.st_mtime_ns, st.st_nlink)
        expected = (md.device, md.inode, md.mode, md.uid, md.gid, md.size, md.mtime_ns, md.nlink)
        if (actual != expected or st.st_dev != os.fstat(rootfd).st_dev
                or hashlib.sha256(raw).hexdigest() != expectation.state.content_sha256):
            _reject('file/path mismatch')
        return BoundBlob(expectation.path, git_blob_oid(raw, object_format),
                         0o100755 if md.mode & 0o100 else 0o100644)


def _attribute_sources(rootfd, paths):
    result = []
    for path in sorted({''} | {p.rsplit('/', 1)[0] for p in paths if '/' in p}):
        with ExitStack() as stack:
            parent = rootfd
            directories = [''] + path.split('/') if path else ['']
            for part in directories:
                if part:
                    parent = _open_dir(stack, part, parent)
                if '.gitattributes' in os.listdir(parent):
                    raw, st = _read_file(parent, '.gitattributes', METADATA_LIMIT, 'unsafe attribute')
                    result.append((path, part, _fingerprint(st), hashlib.sha256(raw).hexdigest()))
    return tuple(result)


def _tree(raw, object_format):
    entries = []
    for record in raw.split(b'\0'):
        if not record:
            continue
        info, sep, path = record.partition(b'\t')
        fields = info.split(b' ')
        if not sep or len(fields) != 3 or fields[1] != b'blob':
            _reject('unsupported repo layout')
        mode, _, oid = fields
        if mode not in (b'100644', b'100755', b'120000') or not re.fullmatch(
                rb'[0-9a-f]{'+(b'40' if object_format == 'sha1' else b'64')+b'}', oid):
            _reject('unsupported repo layout')
        entries.append((path, int(mode, 8), oid.decode('ascii')))
    return tuple(sorted(entries))


def preflight_git_add(action):
    """Verify a typed ADD proposal; never execute it or grant approval.

    Every return value is evidence for a *future* caller, not an execution token.
    No ApprovalValidation parameter exists. All queries are fixed and read-only.
    """
    if type(action) is not contract.Action:
        _reject('action mismatch')
    try:
        # Revalidate detached canonical data, including intentionally tampered
        # frozen dataclasses. Neither parsed data nor USER source is authority.
        action = contract.parse_action(action.to_json())
    except (LLMToolError, ValueError, TypeError, AttributeError):
        raise GitPreflightError('action mismatch') from None
    if action.kind is not contract.ActionKind.GIT_OPERATION or type(action.payload) is not contract.GitAddPayload:
        _reject('action mismatch')
    if action.payload.operation is not contract.GitOperation.ADD:
        _reject('action mismatch')
    category = 'repository identity mismatch'
    try:
        with ExitStack() as stack:
            rootfd = _root(stack, action.repository)
            root_identity = _fingerprint(os.fstat(rootfd))
            category = 'unsupported repo layout'
            gitfd = _open_dir(stack, '.git', rootfd, category=category)
            git_identity = _fingerprint(os.fstat(gitfd))
            metadata = _metadata_snapshot(gitfd)
            # Refuse unsafe config files before the no-includes query opens them.
            _read_file(gitfd, 'config', METADATA_LIMIT, 'unsafe config')
            reads = _Reads(action)
            _check_config(reads.query(_Read.CONFIG))
            fmt = reads.query(_Read.FORMAT).rstrip(b'\n')
            if fmt not in (b'sha1', b'sha256'):
                _reject('unsupported repo layout')
            object_format = fmt.decode('ascii')
            category = 'index mismatch'
            index_raw, _ = _read_file(gitfd, 'index', INDEX_LIMIT, category)
            index_sha = _index_digest(index_raw)
            if index_sha != action.payload.expected_index_sha256 or index_sha != action.expected_state.index_sha256:
                _reject(category)
            index = _parse_index(index_raw, object_format)
            if any(e.stage for e in index):
                _reject('conflict')
            if any(e.skip_worktree or e.intent_to_add or e.assume_unchanged or e.mode == 0o160000 for e in index):
                _reject('unsupported index state')
            head = reads.query(_Read.HEAD).rstrip(b'\n').decode('ascii')
            branch = reads.query(_Read.BRANCH)
            if not branch.startswith(b'refs/heads/') or head != action.repository.expected_head:
                _reject('HEAD mismatch')
            if _tree(reads.query(_Read.TREE), object_format) != tuple((e.path, e.mode, e.oid) for e in index):
                _reject('unsupported index state')  # ANY pre-staged changes, N1 unconditional.
            paths = tuple(f.path for f in action.payload.files)
            # Disk and index spellings must agree; reject normalization/case aliases.
            def alias(raw):
                return unicodedata.normalize('NFC', os.fsdecode(raw)).casefold()
            for path in paths:
                exact = os.fsencode(path)
                if any(e.path != exact and alias(e.path) == alias(exact) for e in index):
                    _reject('file/path mismatch')
            category = 'file/path mismatch'
            blobs = tuple(_target(rootfd, f, object_format) for f in action.payload.files)
            attr_sources = _attribute_sources(rootfd, paths)
            attrs = reads.query(_Read.ATTRIBUTES).split(b'\0')
            if attrs[-1:] != [b''] or len(attrs[:-1]) != len(paths)*len(ATTRIBUTES)*3:
                _reject('unsafe attribute')
            expected_pairs = {(os.fsencode(p), a.encode()) for p in paths for a in ATTRIBUTES}
            seen = set()
            for i in range(0, len(attrs)-1, 3):
                p, attr, value = attrs[i:i+3]
                if (p, attr) not in expected_pairs or (p, attr) in seen:
                    _reject('unsafe attribute')
                seen.add((p, attr))
                if value not in (b'unset', b'unspecified'):
                    _reject('unsafe filter' if attr == b'filter' else 'unsafe attribute')
            # ls-files --others --ignored excludes tracked paths. Any returned target
            # is therefore an ignored untracked target; never parse/echo prose.
            if reads.query(_Read.IGNORE):
                _reject('file/path mismatch')
            # Native flags + exact index/HEAD equality prove absence of staged
            # changes/conflicts. Bound fd reads prove target existence/content.
            # Do not call status/diff: their worktree content comparison can run
            # a clean filter introduced after the initial config/attribute gate.
            # Rewalk/re-read from the namespace to detect replacements and drift.
            checkroot = _root(stack, action.repository)
            checkgit = _open_dir(stack, '.git', checkroot, category='unsupported repo layout')
            if (_fingerprint(os.fstat(checkroot)) != root_identity
                    or _fingerprint(os.fstat(checkgit)) != git_identity
                    or _metadata_snapshot(checkgit) != metadata
                    or hash_git_index(action.repository) != index_sha
                    or _attribute_sources(checkroot, paths) != attr_sources
                    or tuple(_target(checkroot, f, object_format) for f in action.payload.files) != blobs
                    or reads.query(_Read.HEAD).rstrip(b'\n').decode('ascii') != head
                    or reads.query(_Read.BRANCH) != branch):
                _reject('repository identity mismatch')
            return GitAddPreflight(action.digest, action.repository.canonical_root,
                action.repository.root_device, action.repository.root_inode, head, object_format,
                index_sha, paths, action.expected_state.digest, index, blobs,
                _prefix()+('add', '--')+paths,
                tuple(sorted(_environment(action.repository.canonical_root, False).items())))
    except GitPreflightError:
        raise
    except LLMToolError:
        raise GitPreflightError('Git inspection unavailable') from None
    except (OSError, UnicodeError, ValueError):
        raise GitPreflightError(category) from None
