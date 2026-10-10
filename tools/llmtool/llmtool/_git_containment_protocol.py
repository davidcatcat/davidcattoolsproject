"""Private, bounded fake-containment wire protocol; no executable payloads."""
import json
import hashlib
import importlib.util
import math
import os
from pathlib import Path
import select
import stat
import struct
import sys
import time

LIMIT = 128 * 1024
VERSION = 1
CASES = frozenset(('exit', 'descendant', 'leader_exit', 'output', 'hung',
                   'lost_before_identity', 'malformed_status', 'closed_status',
                   'registration_failure', 'stderr_output', 'drain_failure', 'wait',
                   'unresolved', 'anchor_race'))
OUTCOMES = frozenset(('complete', 'start-failed', 'timeout', 'interrupted',
                      'parent-lost', 'supervisor-lost', 'output-limit',
                      'protocol-failure', 'state-unknown'))
# Only reviewed fixed messages may enter curated diagnostics. Exception str()
# can contain paths, argv, credentials or raw output even in a cleanup fault.
_SAFE_MESSAGES = frozenset((
    'invalid containment protocol', 'containment peer closed',
    'containment read deadline', 'containment write deadline',
    'Darwin non-root host required', 'kernel identity unavailable',
    'invalid kernel identity', 'invalid process identity', 'kernel identity changed',
    'single-thread default-reaper host required', 'native single-thread host required',
    'non-reaping Darwin notification required', 'invalid non-reaping exit notification',
    'supervisor anchor ownership unknown', 'owned zombie continuity unconfirmed',
    'shared synthetic cleanup deadline required', 'synthetic anchor deadline expired',
    'synthetic anchor exit notification unavailable', 'synthetic anchor zombie unconfirmed',
    'reap ordering rejected', 'reviewed standalone harness host required',
    'missing supervisor anchor', 'missing group disappearance proof',
    'containment/state unknown; user action required',
    'probe host ownership unknown', 'probe host retained unresolved ownership',
    'probe group disappearance unconfirmed', 'probe evidence deadline',
    'mandatory Mac capability unsupported or unresolved',
    'probe parent closed before expected evidence', 'probe evidence output limit',
    'containment acceptance assertion failed', 'probe event count limit',
    'fixture launch identity unavailable', 'fixture group disappearance unconfirmed',
    'isolated trusted supervisor installation unavailable',
    'unsupported kernel identity query',
))
FIELDS = {
    'START': {'binding', 'parent', 'launch', 'nonce', 'case', 'timeout', 'pipes'},
    'READY': {'digest', 'supervisor', 'nonce'},
    'CHILD_IDENTITY': {'child', 'nonce'},
    'CANCEL': {'nonce'}, 'CANCEL_ACK': {'nonce'},
    'TERMINAL': {'outcome', 'nonce', 'evidence'},
}


class ProtocolFailure(Exception):
    """Fixed text only; never echo rejected data."""


def exception_evidence(error):
    """Bounded type/message/location, with no locals, source text or chaining.

    OS errno supplies fixed text instead of OSError filenames/strerror supplied
    by a caller. Unknown messages are withheld, never truncated raw secrets.
    Trace locations name only reviewed modules; external paths are omitted.
    """
    kind = type(error).__name__
    kind = kind[:64] if kind.isidentifier() else 'Exception'
    message = 'details withheld'
    if len(error.args) == 1 and type(error.args[0]) is str and error.args[0] in _SAFE_MESSAGES:
        message = error.args[0]
    number = getattr(error, 'errno', None) if isinstance(error, OSError) else None
    if type(number) is int and 0 < number < 256:
        message = os.strerror(number)[:160]
    else:
        number = None
    locations = []
    allowed = {name for name in ARTIFACTS if not name.startswith('../')}
    allowed.update(('test_git_containment.py', 'run_containment_acceptance.py',
                    'containment_probe_parent.py'))
    trace = error.__traceback__
    while trace is not None:
        name = Path(trace.tb_frame.f_code.co_filename).name
        function = trace.tb_frame.f_code.co_name
        locations.append({'file': name if name in allowed else '<external>',
            'function': function[:64] if name in allowed else '<external>',
            'line': trace.tb_lineno})
        locations = locations[-8:]
        trace = trace.tb_next
    result = {'exception_type': kind, 'message': message, 'errno': number,
              'traceback': locations}
    # Only the reviewed numeric C-call metadata may cross this boundary. Do
    # not copy arbitrary attributes, API buffers, paths, argv or error strings.
    api = getattr(error, 'libproc', None)
    keys = {'flavor', 'arg', 'requested_bytes', 'returned_bytes', 'errno'}
    if (type(api) is dict and set(api) == keys
            and all(type(value) is int for value in api.values())
            and (api['flavor'], api['arg']) in ((3, 1), (4, 0))
            and 0 < api['requested_bytes'] <= 4096
            and -(2**31) <= api['returned_bytes'] < 2**31
            and 0 <= api['errno'] < 2**31):
        result['libproc'] = dict(api)
        result['errno'] = api['errno']
    return result


def reject():
    raise ProtocolFailure('invalid containment protocol')


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            reject()
        value[key] = item
    return value


def strict_load(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= LIMIT:
        reject()
    try:
        text = raw.decode('utf-8')
        depth, quoted, escaped = 0, False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in '[{':
                depth += 1
                if depth > 8:
                    reject()
            elif char in ']}':
                depth -= 1
        value = json.loads(text, object_pairs_hook=_pairs,
                           parse_constant=lambda _: reject())
        count = 0
        def walk(item):
            nonlocal count
            count += 1
            if count > 4096:
                reject()
            if type(item) is dict:
                for key, child in item.items():
                    walk(key)
                    walk(child)
            elif type(item) is list:
                for child in item:
                    walk(child)
            elif type(item) is float and not math.isfinite(item):
                reject()
            elif item is not None and type(item) not in (str, int, float, bool):
                reject()
        walk(value)
        return value
    except (UnicodeError, ValueError, RecursionError):
        reject()


def canonical(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True, allow_nan=False).encode('ascii')
        strict_load(raw)
        return raw
    except (TypeError, ValueError, RecursionError):
        reject()


def validate(message):
    if type(message) is not dict:
        reject()
    kind = message.get('type')
    if (type(kind) is not str or kind not in FIELDS
            or type(message.get('version')) is not int
            or message['version'] != VERSION
            or set(message) != FIELDS[kind] | {'type', 'version'}):
        reject()
    nonce = message.get('nonce')
    if (type(nonce) is not str or len(nonce) != 64
            or any(c not in '0123456789abcdef' for c in nonce)):
        reject()
    if kind == 'START':
        if (type(message['case']) is not str or message['case'] not in CASES
                or type(message['timeout']) not in (int, float)
                or not 0 < message['timeout'] <= 30
                or type(message['binding']) is not dict
                or type(message['parent']) is not dict
                or type(message['pipes']) is not list
                or len(message['pipes']) != 2):
            reject()
        identity_data(message['parent'])
        identity_data(message['launch'])
        for endpoint in message['pipes']:
            if (type(endpoint) is not list or len(endpoint) != 2
                    or any(type(v) is not int or v < 0 for v in endpoint)):
                reject()
    elif kind == 'READY':
        identity_data(message['supervisor'])
        hex_digest(message['digest'])
    elif kind == 'CHILD_IDENTITY':
        identity_data(message['child'])
    if kind == 'TERMINAL' and (type(message['outcome']) is not str
                             or message['outcome'] not in OUTCOMES):
        reject()
    if kind == 'TERMINAL':
        child_evidence(message['evidence'])
    return message


def hex_digest(value):
    if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        reject()


def identity_data(value):
    names = {'pid', 'pgid', 'sid', 'uid', 'ppid', 'seconds', 'micros'}
    if (type(value) is not dict or set(value) != names
            or any(type(v) is not int or v < 0 for v in value.values())
            or any(value[k] == 0 for k in ('pid', 'pgid', 'sid', 'seconds'))
            or value['micros'] >= 1000000):
        reject()


def child_evidence(value):
    if value == {}:
        return
    if (type(value) is not dict or set(value) != {'child', 'descendants', 'fds'}
            or type(value['descendants']) is not list or len(value['descendants']) > 1
            or type(value['fds']) is not list or len(value['fds']) > 64):
        reject()
    identity_data(value['child'])
    for item in value['descendants']:
        identity_data(item)
    for fd in value['fds']:
        if (type(fd) is not dict or set(fd) != {'fd', 'device', 'inode'}
                or any(type(v) is not int or v < 0 for v in fd.values())):
            reject()


def message(kind, **fields):
    return validate({'version': VERSION, 'type': kind, **fields})


def frame(value):
    raw = canonical(validate(value))
    return struct.pack('>I', len(raw)) + raw


class Reader:
    """Incremental reader never reserves a peer-declared unbounded buffer."""
    def __init__(self, fd, *, validate_message=True):
        self.fd, self.buffer, self.size = fd, bytearray(), None
        self.validate_message = validate_message
        os.set_blocking(fd, False)

    def read(self):
        while True:
            target = 4 if self.size is None else self.size
            try:
                part = os.read(self.fd, target - len(self.buffer))
            except BlockingIOError:
                return None
            if not part:
                if self.buffer or self.size is not None:
                    reject()
                raise EOFError('containment peer closed')
            self.buffer.extend(part)
            if len(self.buffer) < target:
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
                value = strict_load(raw)
                return validate(value) if self.validate_message else value

    def receive(self, deadline):
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError('containment read deadline')
            value = self.read()
            if value is not None:
                if time.monotonic() >= deadline:
                    raise TimeoutError('containment read deadline')
                return value
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('containment read deadline')
            select.select([self.fd], [], [], min(remaining, .02))


def send(fd, value, deadline):
    os.set_blocking(fd, False)
    raw = memoryview(frame(value))
    while raw:
        if time.monotonic() >= deadline:
            raise TimeoutError('containment write deadline')
        try:
            count = os.write(fd, raw)
            if count <= 0:
                raise BrokenPipeError('containment peer closed')
            raw = raw[count:]
        except BlockingIOError:
            select.select([], [fd], [], min(.02, max(0, deadline-time.monotonic())))


ARTIFACTS = ('__init__.py', 'git_mutation.py', 'git_intel.py',
             'action_contract.py', 'errors.py', '_git_containment.py',
             '_git_supervisor.py', '_git_containment_protocol.py',
             '_git_process_identity.py', '_git_containment_child.py',
             '../tests/containment_probe_parent.py')


def file_hash(path):
    path = Path(path)
    # Artifact paths must be direct trusted files; interpreter aliases are
    # resolved explicitly by runtime_binding, never implicitly by this reader.
    if path.resolve() != Path(os.path.abspath(path)):
        reject()
    # Trusted installation/administration is a deployment premise. These
    # file/origin/digest checks detect drift, not privilege isolation from an
    # administrator able to replace a Homebrew installation directory.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        stream = os.fdopen(fd, 'rb')
    except BaseException:
        # Wrapping can fail (notably for a directory). Preserve that exception
        # even if closing the still-raw owned descriptor also fails.
        try:
            os.close(fd)
        except BaseException:
            pass
        raise
    with stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_mode & 0o022
                or before.st_uid not in (0, os.geteuid()) or before.st_size > 64*1024*1024):
            reject()
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    named = os.stat(path, follow_symlinks=False)
    def signature(st):
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    if signature(before) != signature(after) or signature(after) != signature(named):
        reject()
    return digest.hexdigest()


def runtime_binding(pinned_manifest, case='exit', timeout=2):
    """The internal caller pins reviewed bytes before entering the host window.

    This takes no Action or Approval. Synthetic action/preflight domain digests
    bind only the fixed engineering fixture, never product permission or audit.
    """
    root = Path(__file__).resolve().parent
    if type(pinned_manifest) is not dict or set(pinned_manifest) != set(ARTIFACTS):
        reject()
    for digest in pinned_manifest.values():
        hex_digest(digest)
    if (type(case) is not str or case not in CASES
            or type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 0 < timeout <= 30):
        reject()
    actual = {}
    for name in ARTIFACTS:
        if not name.startswith('../tests/'):
            module = 'llmtool' if name == '__init__.py' else 'llmtool.' + name[:-3]
            spec = importlib.util.find_spec(module)
            if spec is None or Path(spec.origin).resolve() != root / name:
                reject()
            loaded = sys.modules.get(module)
            if loaded is not None and Path(loaded.__file__).resolve() != root / name:
                reject()
        actual[name] = file_hash(root / name)
    if actual != pinned_manifest:
        reject()
    executable = os.path.abspath(sys.executable)
    interpreter = os.path.realpath(executable)
    fixture = {'case': case, 'timeout': timeout}
    return {'manifest': actual, 'installation': str(root),
            'executable': executable, 'interpreter': interpreter,
            'interpreter_sha256': file_hash(interpreter),
            'protocol': VERSION,
            'fixture': fixture,
            'action_digest': hashlib.sha256(canonical({
                'domain': 'A11b.2.2.1/fake-only/action/v1', 'fixture': fixture})).hexdigest(),
            'preflight_digest': hashlib.sha256(canonical({
                'domain': 'A11b.2.2.1/fake-only/preflight/v1',
                'fixture': fixture, 'manifest': actual})).hexdigest()}


def verify_binding(binding):
    if type(binding) is not dict or set(binding) != {
            'manifest', 'installation', 'executable', 'interpreter',
            'interpreter_sha256', 'protocol', 'fixture', 'action_digest', 'preflight_digest'}:
        reject()
    if (type(binding['protocol']) is not int or binding['protocol'] != VERSION
            or type(binding['fixture']) is not dict
            or set(binding['fixture']) != {'case', 'timeout'}):
        reject()
    for key in ('installation', 'executable', 'interpreter'):
        if type(binding[key]) is not str:
            reject()
    for key in ('interpreter_sha256', 'action_digest', 'preflight_digest'):
        hex_digest(binding[key])
    if runtime_binding(binding['manifest'], **binding['fixture']) != binding:
        reject()
    return hashlib.sha256(canonical(binding)).hexdigest()


def fresh_environment():
    return {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}
