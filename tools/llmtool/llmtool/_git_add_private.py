"""F1 disposable-fixture adapter, never a CLI/provider/approval API.

Same-UID trusted installation is a premise. A nonce, manifest and FIFO prove
continuity, not malicious same-UID caller authentication. Only run_fixture_add
creates mutation targets; it accepts data, never a repo, command or callback.
"""
from contextlib import ExitStack
from dataclasses import fields, is_dataclass
from enum import Enum
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import select
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time

from . import action_contract as c
from . import git_mutation as g
from . import _git_containment_protocol as wire
from . import _git_process_identity as kernel
from .errors import LLMToolError

DOMAIN = 'A11b.2.2.2/git-add/internal/v1'
LIMIT, BINDING_LIMIT, PRIVATE_FD = 131072, 16384, 3
MARKER = '.llmtool-f1-fixture'
CANONICAL = '/Users/davidsun/git/davidcataiporject/davidcattoolsproject'
ARTIFACTS = ('__init__.py', 'errors.py', 'action_contract.py', 'git_mutation.py',
             '_git_containment_protocol.py', '_git_process_identity.py',
             '_git_supervisor.py', '_git_add_private.py', '_git_add_host.py')
FRAME_KEYS = {'domain', 'version', 'nonce', 'parent', 'runtime', 'fixture',
              'action', 'action_digest', 'preflight_digest', 'timeout', 'pipe'}


class AdmissionRejected(Exception):
    def __init__(self):
        super().__init__('AUTHORITY_REJECTED')


def reject():
    raise AdmissionRejected()


def _serialized(value):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'),
                         sort_keys=True, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        reject()


def canonical(value):
    try:
        raw = _serialized(value)
        wire.strict_load(raw)
        return raw
    except (ValueError, TypeError, UnicodeError, RecursionError, wire.ProtocolFailure):
        reject()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _data(value):
    if is_dataclass(value):
        return {f.name: _data(getattr(value, f.name)) for f in fields(value)}
    if type(value) is bytes:
        return value.hex()
    if type(value) is tuple:
        return [_data(v) for v in value]
    return value


def preflight_digest(plan):
    # A complete semantic index may exceed the transport cap. It never enters
    # a frame: hash its bounded local evidence with the identical UTF-8 JSON
    # serializer, binding both the complete baseline and exact expected state.
    raw = _serialized({'preflight': _data(plan), 'expected_index': _data(expected_index(plan))})
    if len(raw) > 16*g.METADATA_LIMIT:
        reject()
    return hashlib.sha256(raw).hexdigest()


def validate_frame(value):
    if (type(value) is not dict or set(value) != FRAME_KEYS
            or value['domain'] != DOMAIN or type(value['version']) is not int
            or value['version'] != 1 or type(value['action']) is not dict
            or type(value['timeout']) not in (int, float)
            or not math.isfinite(value['timeout']) or not 0 < value['timeout'] <= 30
            or type(value['pipe']) is not list or len(value['pipe']) != 2
            or any(type(v) is not int or v < 0 for v in value['pipe'])):
        reject()
    wire.hex_digest(value['nonce'])
    wire.hex_digest(value['action_digest'])
    wire.hex_digest(value['preflight_digest'])
    kernel.Identity.parse(value['parent'])
    metadata = {k: v for k, v in value.items() if k != 'action'}
    if len(canonical(metadata)) > BINDING_LIMIT:
        reject()
    raw = canonical(value['action'])
    if len(raw) > c.JSON_LIMIT:
        reject()
    try:
        action = c.parse_action(raw.decode('utf-8'))
        if (action.kind is not c.ActionKind.GIT_OPERATION
                or action.digest != value['action_digest']
                or tuple(action.scope) != tuple(f.path for f in action.payload.files)):
            reject()
    except LLMToolError:
        reject()
    if len(canonical(value)) > LIMIT:
        reject()
    return action


def encode_frame(value):
    validate_frame(value)
    raw = canonical(value)
    return struct.pack('>I', len(raw)) + raw


def write_bytes(fd, raw, deadline):
    os.set_blocking(fd, False)
    remaining = memoryview(raw)
    while remaining:
        if time.monotonic() >= deadline:
            raise TimeoutError('private frame deadline')
        try:
            count = os.write(fd, remaining)
            if count <= 0:
                raise BrokenPipeError()
            remaining = remaining[count:]
        except BlockingIOError:
            select.select([], [fd], [], min(.01, max(0, deadline-time.monotonic())))


class Reader:
    """One bounded frame at a time; no sender-controlled allocation."""
    def __init__(self, fd):
        self.fd, self.buffer, self.size = fd, bytearray(), None
        os.set_blocking(fd, False)

    def read(self):
        while True:
            target = 4 if self.size is None else self.size
            try:
                chunk = os.read(self.fd, target-len(self.buffer))
            except BlockingIOError:
                return None
            if not chunk:
                if self.size is not None or self.buffer:
                    reject()
                raise EOFError()
            self.buffer.extend(chunk)
            if len(self.buffer) != target:
                continue
            if self.size is None:
                self.size = struct.unpack('>I', self.buffer)[0]
                self.buffer.clear()
                if not 0 < self.size <= LIMIT:
                    reject()
            else:
                raw = bytes(self.buffer)
                self.buffer.clear()
                self.size = None
                value = wire.strict_load(raw)
                if canonical(value) != raw:
                    reject()
                return value

    def receive(self, deadline):
        while time.monotonic() < deadline:
            value = self.read()
            if value is not None:
                if time.monotonic() >= deadline:
                    raise TimeoutError('private frame deadline')
                return value
            select.select([self.fd], [], [], min(.01, max(0, deadline-time.monotonic())))
        raise TimeoutError('private frame deadline')

    def eof(self, deadline):
        while time.monotonic() < deadline:
            try:
                chunk = os.read(self.fd, 1)
            except BlockingIOError:
                select.select([self.fd], [], [], min(.01, max(0, deadline-time.monotonic())))
                continue
            if chunk:
                reject()
            return
        raise TimeoutError('private frame deadline')


def send(fd, value, deadline):
    write_bytes(fd, struct.pack('>I', len(canonical(value)))+canonical(value), deadline)


def endpoints(expected):
    """/dev/fd listing may itself briefly open an FD; fstat filters closed rows."""
    actual = {}
    for name in os.listdir('/dev/fd'):
        try:
            fd = int(name)
            st = os.fstat(fd)
        except (ValueError, OSError):
            continue
        if fd > 2:
            if not stat.S_ISFIFO(st.st_mode):
                reject()
            actual[fd] = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
    if actual != expected:
        reject()
    for fd in actual:
        os.set_inheritable(fd, False)


def runtime():
    root = Path(__file__).resolve().parent
    manifest = {}
    for name in ARTIFACTS:
        module = 'llmtool' if name == '__init__.py' else 'llmtool.'+name[:-3]
        spec = importlib.util.find_spec(module)
        if spec is None or Path(spec.origin).absolute() != root/name:
            reject()
        loaded = sys.modules.get(module)
        if loaded is not None and Path(loaded.__file__).absolute() != root/name:
            reject()
        manifest[name] = wire.file_hash(root/name)
    interpreter = os.path.realpath(sys.executable)
    return {'installation': str(root), 'executable': os.path.abspath(sys.executable),
            'interpreter': interpreter, 'interpreter_sha256': wire.file_hash(interpreter),
            'manifest': manifest}


def verify_runtime(bound):
    if bound != runtime():
        reject()


def standalone(module):
    root = Path(__file__).resolve().parent
    target = root/(module+'.py')
    main = sys.modules.get('__main__')
    if (not sys.flags.isolated or len(sys.argv) != 1
            or Path(sys.argv[0]).absolute() != target
            or Path(getattr(main, '__file__', '')).absolute() != target
            or sys.gettrace() is not None or sys.getprofile() is not None
            or sys.version_info < (3, 9)):
        raise kernel.NotSupported('private standalone host required')
    kernel.host_check()


def fixture_check(bound, action, nonce):
    """Independent no-follow target guard; marker is provenance, not a secret."""
    if type(bound) is not dict or set(bound) != {'root', 'device', 'inode', 'marker'}:
        reject()
    temps = {os.path.realpath(tempfile.gettempdir())}
    if sys.platform == 'darwin':
        # Fresh Host/Supervisor environments intentionally omit TMPDIR. Darwin
        # supplies the same per-UID OS temp directory independently via confstr.
        # _CS_DARWIN_USER_TEMP_DIR=65537 (installed SDK unistd.h); Python's
        # confstr_names need not expose its symbolic name. No sender assertion
        # or arbitrary environment-selected directory adds a trusted temp root.
        native_temp = os.confstr(65537)
        if not native_temp:
            reject()
        temps.add(os.path.realpath(native_temp))
    root = bound['root']
    repo = action.repository.canonical_root
    if (type(root) is not str or root != os.path.realpath(root)
            or os.path.dirname(root) not in temps
            or not os.path.basename(root).startswith('llmtool-f1-')
            or repo != root+'/repo' or repo != os.path.realpath(repo)):
        reject()
    for protected in (os.path.realpath(os.path.expanduser('~')), os.path.realpath(CANONICAL)):
        if os.path.commonpath((root, protected)) in (root, protected):
            reject()
    try:
        with ExitStack() as stack:
            # _root traverses *every* normalized component using O_NOFOLLOW.
            repo_fd = g._root(stack, action.repository)
            root_fd = g._open_dir(stack, root, category='repository identity mismatch')
            st = os.fstat(root_fd)
            if (st.st_dev, st.st_ino) != (bound['device'], bound['inode']):
                reject()
            project = os.stat(CANONICAL)
            if (project.st_dev, project.st_ino) in ((st.st_dev, st.st_ino),
                    (os.fstat(repo_fd).st_dev, os.fstat(repo_fd).st_ino)):
                reject()
            marker, ms = g._read_file(root_fd, MARKER, 64, 'repository identity mismatch')
            git_fd = g._open_dir(stack, '.git', repo_fd, category='unsupported repo layout')
            gs = os.fstat(git_fd)
            signature = list(g._fingerprint(ms))
            birth = getattr(gs, 'st_birthtime', None)
            # Darwin birth time is float-valued: require a >1us separation to
            # avoid claiming creation order inside its rounding uncertainty.
            if (marker != nonce.encode('ascii') or stat.S_IMODE(ms.st_mode) != 0o600
                    or signature != bound['marker'] or birth is None
                    or ms.st_ctime_ns/1e9+0.000001 >= birth):
                reject()
            if g._Reads(action).query(g._Read.TOPLEVEL).rstrip(b'\n') != os.fsencode(repo):
                reject()
    except (OSError, g.GitPreflightError):
        reject()


def executable_surface(action):
    """F1 only: reject all config beyond the self-created baseline vocabulary.

    The A11b.2.1 pins neutralize hooks/fsmonitor/global attributes, maintenance,
    network and credentials. This stricter fixture gate also refuses local
    helper declarations, includes, attributes and linked/sparse/alternate repos.
    No status/diff/content query runs a clean filter.
    """
    allowed = {b'core.repositoryformatversion', b'core.filemode', b'core.bare',
               b'core.logallrefupdates', b'core.ignorecase', b'core.precomposeunicode',
               b'user.name', b'user.email'}
    raw = g._Reads(action).query(g._Read.CONFIG)
    g._check_config(raw)
    if any(row.partition(b'\n')[0].lower() not in allowed
           for row in raw.split(b'\0') if row):
        reject()
    with ExitStack() as stack:
        rootfd = g._root(stack, action.repository)
        gitfd = g._open_dir(stack, '.git', rootfd, category='unsupported repo layout')
        g._metadata_snapshot(gitfd)
        info = g._open_dir(stack, 'info', gitfd, category='unsupported repo layout')
        if 'attributes' in os.listdir(info):
            reject()
        if g._attribute_sources(rootfd, tuple(action.scope)):
            reject()


def verify_request(value):
    action = validate_frame(value)
    verify_runtime(value['runtime'])
    fixture_check(value['fixture'], action, value['nonce'])
    executable_surface(action)
    plan = g.preflight_git_add(action)
    if preflight_digest(plan) != value['preflight_digest']:
        reject()
    return action, plan


def expected_index(plan):
    targets = {os.fsencode(b.path): g.IndexEntry(os.fsencode(b.path), b.mode, b.oid,
                0, False, False, False) for b in plan.bound_blobs}
    result = {e.path: e for e in plan.semantic_index}
    result.update(targets)
    return tuple(result[p] for p in sorted(result))


def classify(pre, expected, post, *, contained, safe_index=True, safe_repo=True,
             exit_code=None, interrupted=True):
    if not contained:
        return 'STATE_UNKNOWN'
    if not safe_index or not safe_repo:
        return 'USER_ACTION_REQUIRED'
    if post == expected:
        return 'APPLIED' if exit_code == 0 and not interrupted else 'APPLIED_BUT_CONTROL_INTERRUPTED'
    if post == pre and pre != expected:
        return 'NOT_APPLIED'
    return 'USER_ACTION_REQUIRED'


def post_state(action, plan, *, exit_code, interrupted):
    safe_index = safe_repo = True
    post, post_sha = None, None
    try:
        with ExitStack() as stack:
            rootfd = g._root(stack, action.repository)
            gitfd = g._open_dir(stack, '.git', rootfd, category='unsupported repo layout')
            if 'index.lock' in os.listdir(gitfd):
                raise g.GitPreflightError('index mismatch')
            raw, _ = g._read_file(gitfd, 'index', g.INDEX_LIMIT, 'index mismatch')
            post, post_sha = g._parse_index(raw, plan.object_format), hashlib.sha256(raw).hexdigest()
    except (OSError, g.GitPreflightError):
        safe_index = False
    if safe_index:
        try:
            with ExitStack() as stack:
                rootfd = g._root(stack, action.repository)
                gitfd = g._open_dir(stack, '.git', rootfd, category='unsupported repo layout')
                st = os.fstat(gitfd)
                g._metadata_snapshot(gitfd)
                reads = g._Reads(action)
                safe_repo = ((st.st_dev, st.st_ino) == plan.git_identity
                    and reads.query(g._Read.HEAD).rstrip(b'\n').decode('ascii') == plan.head
                    and reads.query(g._Read.BRANCH).rstrip(b'\n').decode('utf-8') == plan.branch
                    and reads.query(g._Read.TOPLEVEL).rstrip(b'\n') == os.fsencode(plan.repository_root)
                    and hashlib.sha256(g._read_file(gitfd, 'config', g.METADATA_LIMIT,
                        'unsafe config')[0]).hexdigest() == plan.config_sha256
                    and hashlib.sha256(repr(g._attribute_sources(rootfd, plan.paths)).encode('utf-8')).hexdigest() == plan.attributes_sha256
                    and g.hash_git_index(action.repository) == post_sha)
        except (OSError, UnicodeError, g.GitPreflightError):
            safe_repo = False
    expected = expected_index(plan)
    return {'outcome': classify(plan.semantic_index, expected, post, contained=True,
                safe_index=safe_index, safe_repo=safe_repo, exit_code=exit_code,
                interrupted=interrupted), 'containment_confirmed': True,
            'no_op': expected == plan.semantic_index, 'post_index_sha256': post_sha,
            'preflight_digest': preflight_digest(plan), 'exit_code': exit_code}


class _Setup(Enum):
    INIT = 1
    NAME = 2
    EMAIL = 3
    MODE = 4
    BASELINE = 5
    COMMIT = 6
    HEAD = 7


def _fixture_git(root, operation, paths=()):
    # Closed fixture setup vocabulary, never an arbitrary Git command API.
    if type(operation) is not _Setup:
        reject()
    if operation is _Setup.BASELINE:
        c._scope(paths)
    elif paths:
        reject()
    args = {_Setup.INIT: ('init', '-q', '--template='),
        _Setup.NAME: ('config', 'user.name', 'F1 Fixture'),
        _Setup.EMAIL: ('config', 'user.email', 'fixture@example.invalid'),
        _Setup.MODE: ('config', 'core.fileMode', 'true'),
        _Setup.BASELINE: ('add', '--')+tuple(paths),
        _Setup.COMMIT: ('commit', '-qm', 'F1 disposable baseline'),
        _Setup.HEAD: ('rev-parse', '--verify', 'HEAD^{commit}')}[operation]
    return subprocess.check_output(g._prefix()+args, cwd=root,
        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
             'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_ATTR_NOSYSTEM': '1',
             'GIT_TERMINAL_PROMPT': '0'}, stdin=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=10)


def _write_files(repo, files):
    if type(files) is not dict or not 1 <= len(files) <= c.MAX_FILES:
        reject()
    total = 0
    for path, raw in files.items():
        c._path(path)
        if type(raw) is not bytes:
            reject()
        total += len(raw)
        if total > c.MAX_CONTENT_BYTES:
            reject()
        target = repo/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def terminal_result(value, expected_digest):
    if type(value) is not dict or type(value.get('outcome')) is not str:
        reject()
    if value['outcome'] in ('AUTHORITY_REJECTED', 'NOT_SUPPORTED'):
        if set(value) != {'outcome', 'containment_confirmed'} or value['containment_confirmed'] is not True:
            reject()
        return value
    if (set(value) != {'outcome', 'containment_confirmed', 'no_op', 'post_index_sha256',
            'preflight_digest', 'exit_code', 'code', 'cleanup_errors', 'primary_error'}
            or value['outcome'] not in ('APPLIED', 'APPLIED_BUT_CONTROL_INTERRUPTED',
                                      'NOT_APPLIED', 'USER_ACTION_REQUIRED')
            or value['containment_confirmed'] is not True
            or type(value['no_op']) is not bool or value['preflight_digest'] != expected_digest
            or (value['exit_code'] is not None and type(value['exit_code']) is not int)
            or type(value['code']) is not str or not value['code'].startswith('MUTATION_')
            or type(value['cleanup_errors']) is not list or len(value['cleanup_errors']) > 8
            or (value['primary_error'] is not None and type(value['primary_error']) is not dict)):
        reject()
    if value['post_index_sha256'] is not None:
        wire.hex_digest(value['post_index_sha256'])
    return value


def _make_fixture(baseline, changes):
    root = Path(tempfile.mkdtemp(prefix='llmtool-f1-', dir=os.path.realpath(tempfile.gettempdir())))
    nonce = os.urandom(32).hex()
    try:
        fd = os.open(root/MARKER, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
        try:
            if os.write(fd, nonce.encode('ascii')) != 64:
                reject()
        finally:
            os.close(fd)
        repo = root/'repo'
        repo.mkdir(mode=0o700)
        _fixture_git(repo, _Setup.INIT)
        (repo/'.git/info').mkdir(exist_ok=True)
        _fixture_git(repo, _Setup.NAME)
        _fixture_git(repo, _Setup.EMAIL)
        _fixture_git(repo, _Setup.MODE)
        _write_files(repo, baseline)
        _fixture_git(repo, _Setup.BASELINE, tuple(sorted(baseline)))
        _fixture_git(repo, _Setup.COMMIT)
        _write_files(repo, changes)
        st = repo.stat()
        head = _fixture_git(repo, _Setup.HEAD).decode('ascii').strip()
        binding = c.RepositoryBinding(digest({'nonce': nonce}), str(repo), st.st_dev, st.st_ino, head)
        files = []
        for path in sorted(changes):
            fs = (repo/path).stat()
            md = c.FileMetadata(fs.st_dev, fs.st_ino, stat.S_IMODE(fs.st_mode),
                fs.st_uid, fs.st_gid, fs.st_size, fs.st_mtime_ns, fs.st_nlink)
            files.append(c.FileExpectation(path, c.FileState(True, c.FileType.REGULAR,
                hashlib.sha256(changes[path]).hexdigest(), md)))
        action = c.Action(1, 'f1-add', int(time.time()), c.ActionKind.GIT_OPERATION,
            c.TaskBinding('A11b222-F1', nonce[:32], 1, digest({'domain': DOMAIN})),
            binding, tuple(sorted(changes)), c.GitAddPayload(c.GitOperation.ADD,
                files, g.hash_git_index(binding)))
        rs, ms = root.stat(), (root/MARKER).stat()
        fixture = {'root': str(root), 'device': rs.st_dev, 'inode': rs.st_ino,
                   'marker': list(g._fingerprint(ms))}
        fixture_check(fixture, action, nonce)
        return root, action, fixture, nonce
    except BaseException:
        shutil.rmtree(root)
        raise


def run_fixture_add(baseline, changes, *, timeout=2):
    """Create a fresh private fixture and stage changes there once.

    No repository parameter, callbacks, fault flags, Approval or release API.
    The caller is synchronous and must satisfy the native single-thread premise.
    On an incident, retain the fixture and host permanently; never blind retry.
    """
    kernel.host_check()
    bound_runtime = runtime()
    root, action, fixture, nonce = _make_fixture(baseline, changes)
    process = None
    fds, saved, saved_inheritable = [], None, False
    try:
        plan = g.preflight_git_add(action)
        request = {'domain': DOMAIN, 'version': 1, 'nonce': nonce,
            'parent': kernel.identity(os.getpid()).data(), 'runtime': bound_runtime,
            'fixture': fixture, 'action': action.to_dict(), 'action_digest': action.digest,
            'preflight_digest': preflight_digest(plan), 'timeout': timeout}
        try:
            os.fstat(PRIVATE_FD)
        except OSError:
            pass
        else:
            saved_inheritable = os.get_inheritable(PRIVATE_FD)
            saved = os.dup(PRIVATE_FD)
        r, w = os.pipe()
        fds.extend((r, w))
        if r != PRIVATE_FD:
            # Move a coinciding writer first; never silently overwrite it.
            if w == PRIVATE_FD:
                moved = os.dup(w)
                os.close(w)
                fds.remove(w)
                w = moved
                fds.append(w)
            os.dup2(r, PRIVATE_FD, inheritable=False)
            os.close(r)
            fds.remove(r)
            r = PRIVATE_FD
            fds.append(r)
        st = os.fstat(r)
        request['pipe'] = [st.st_dev, st.st_ino]
        raw = encode_frame(request)
        process = subprocess.Popen([sys.executable, '-I', '-m', 'llmtool._git_add_host'],
            close_fds=True, pass_fds=(PRIVATE_FD,), shell=False,
            cwd=fixture['root'], env=wire.fresh_environment(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        os.close(r)
        fds.remove(r)
        if saved is not None:
            os.dup2(saved, PRIVATE_FD, inheritable=saved_inheritable)
            os.close(saved)
            saved = None
        deadline = time.monotonic()+2
        write_bytes(w, raw, deadline)
        os.close(w)
        fds.remove(w)
        reader = Reader(process.stdout.fileno())
        result = reader.receive(time.monotonic()+timeout+12)
        if type(result) is not dict or result.get('outcome') == 'STATE_UNKNOWN':
            # Retain host and its disposable repository. No resolver/reap API.
            _retained_wait(process, root)
        terminal_result(result, request['preflight_digest'])
        reader.eof(time.monotonic()+2)
        process.wait(timeout=2)
        result['fixture_provenance_digest'] = digest(fixture)
        return result
    except BaseException:
        if process is not None:
            _retained_wait(process, root)
        raise
    finally:
        for fd in fds:
            os.close(fd)
        if saved is not None:
            os.dup2(saved, PRIVATE_FD, inheritable=saved_inheritable)
            os.close(saved)
        if process is not None and process.stdout is not None:
            process.stdout.close()
        shutil.rmtree(root)


def _retained_wait(process, root):
    # No timer/cancel/ACK/release changes UNKNOWN. External fixture operator
    # termination is the only exit; keep references live without poll/wait.
    while True:
        try:
            select.select([], [], [], .05)
        except BaseException:
            pass


if __name__ == '__main__':
    raise SystemExit(78)
