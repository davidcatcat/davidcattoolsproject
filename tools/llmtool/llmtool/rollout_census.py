"""Bounded structure-only census, independent of Pickup/coverage/authority.

Raw JSONL briefly exists in local memory. Only closed-enum projections leave
this module. A pin is DATA, never permission to read a real private rollout.
No task I/O occurs at import. The production entry validates runtime/code pins
before opening input. Source SHA is a descriptive pin, not a Git attestation.
A caller of the library remains responsible for its separate read authority.

Single foreground invocation: 25s work + reserved 5s cleanup, 5s per operation. A future live
caller must additionally enforce a 60s external envelope and prove actual exit,
including blocked stdout; this module does not spawn a watchdog or any child.
"""
import hashlib
import json
import os
import re
import signal
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .codex_pickup_native import _rollout_json

LINE = 64 * 1024
TOTAL = 512 * 1024
TAIL = 448 * 1024
RECORDS = READS = 128
HISTORY = ('forked_from_id', 'history_base', 'parent_thread_id',
           'forked_from_ordinal_exclusive', 'subagent_history_start_ordinal')
INCOMPLETE = frozenset(('TAIL_ALIGNMENT_UNAVAILABLE', 'BOUNDED_TAIL_SAMPLE',
    'BYTE_LIMIT', 'RECORD_LIMIT', 'READ_LIMIT', 'LINE_OVER_LIMIT',
    'PARTIAL_LAST_LINE', 'SOURCE_GREW'))
UNSUPPORTED = frozenset(('LEGACY_HISTORY', 'FORK_OR_HISTORY_PRESENT',
                        'UNKNOWN_TYPE', 'UNSUPPORTED_SHAPE'))
CODES = frozenset(('PIN_INVALID', 'RUNTIME_MISMATCH', 'INPUT_IDENTITY_MISMATCH',
    'PATH_UNSAFE', 'META_OVER_LIMIT', 'META_INVALID', 'IDENTITY_MISMATCH',
    'SOURCE_CHANGED', 'INVALID_RECORD', 'ORDINAL_INVALID', 'ORDINAL_GAP',
    'DEADLINE', 'CLOSE_FAILED', 'INTERRUPTED', 'OUTPUT_FAILED')) | INCOMPLETE | UNSUPPORTED
EXITS = {'FORMAT_OBSERVED_SUPPORTED': 0, 'INCOMPLETE': 2, 'UNSUPPORTED': 3, 'BLOCKED': 1}
TYPES = ('event_msg', 'response_item', 'session_meta', 'UNKNOWN')
EVENTS = ('task_started', 'task_complete', 'turn_aborted', 'item_completed', 'UNKNOWN')
ITEMS = ('UserMessage', 'AgentMessage', 'Reasoning', 'UNKNOWN')
PHASES = ('commentary', 'final_answer', 'UNKNOWN')
ERRORS = ('ABSENT', 'OBJECT', 'NULL', 'OTHER')
CANONICAL = '/Users/davidsun/git/davidcataiporject/davidcattoolsproject'
CODEX = '/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/codex'
SOURCE_FILES = {'driver': 'tools/llmtool/scripts/native_step0_census.py',
                'module': 'tools/llmtool/llmtool/rollout_census.py',
                'decoder': 'tools/llmtool/llmtool/codex_pickup_native.py'}


class Stop(Exception):
    def __init__(self, code):
        self.code = code if code in CODES else 'PIN_INVALID'
        super().__init__(self.code)


def _stop(code):
    raise Stop(code)


@contextmanager
def _alarm(seconds):
    """Foreground-only Unix bound; never invent a worker/child exit."""
    previous = signal.getsignal(signal.SIGALRM)
    timer = signal.getitimer(signal.ITIMER_REAL)
    if timer[0] != 0:
        _stop('RUNTIME_MISMATCH')
    def expired(signum, frame):
        _stop('DEADLINE')
    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, max(0.001, seconds))
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class Budget:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.start = clock()
        self.deadline = self.start + 30
        self.work_deadline = self.deadline - 5  # Reserve owned cleanup inside 30s.
        self.bytes = self.reads = 0

    def check(self):
        if self.clock() >= min(self.work_deadline, self.deadline):
            _stop('DEADLINE')

    def op(self, function, *args):
        self.check()
        start = self.clock()
        with _alarm(min(5, self.work_deadline - start, self.deadline - start)):
            value = function(*args)
        if self.clock() - start >= 5:
            _stop('DEADLINE')
        self.check()
        return value

    def read(self, fd, count):
        if self.reads >= READS:
            _stop('READ_LIMIT')
        if self.bytes >= TOTAL:
            _stop('BYTE_LIMIT')
        self.reads += 1
        data = self.op(os.read, fd, min(count, TOTAL - self.bytes))
        self.bytes += len(data)
        return data


def _physical(path, budget):
    """No symlinks in any component, including the platform /tmp alias."""
    p = Path(path)
    if not p.is_absolute() or str(p) != os.path.abspath(path) or '..' in p.parts:
        _stop('PATH_UNSAFE')
    for parent in reversed((p,) + tuple(p.parents)):
        st = budget.op(os.lstat, str(parent))
        if stat.S_ISLNK(st.st_mode):
            _stop('PATH_UNSAFE')
        if parent != p and not stat.S_ISDIR(st.st_mode):
            _stop('PATH_UNSAFE')
    return st


def _private(st, mode, uid):
    return (stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_uid == uid
            and stat.S_IMODE(st.st_mode) == mode)


def _identity(st):
    return (st.st_dev, st.st_ino, st.st_uid, st.st_mode, st.st_nlink)


@dataclass(frozen=True)
class InputPin:
    codex_home: str
    path: str
    session_id: str
    thread_id: str
    uid: int
    dev: int
    inode: int
    mode: int
    source_version: str = 'codex-0.162.1-092d3acd'

    def validate(self, budget):
        if (self.source_version != 'codex-0.162.1-092d3acd'
                or any(type(x) is not int or x < 0 for x in (self.uid, self.dev, self.inode, self.mode))
                or self.uid != os.getuid() or self.mode != 0o600
                or any(type(x) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', x)
                       for x in (self.session_id, self.thread_id))):
            _stop('PIN_INVALID')
        try:
            home, path = Path(self.codex_home), Path(self.path)
            parts = path.relative_to(home).parts
        except Exception:
            _stop('PATH_UNSAFE')
        if (len(parts) != 5 or parts[0] != 'sessions'
                or not re.fullmatch(r'\d{4}', parts[1])
                or any(not re.fullmatch(r'\d{2}', x) for x in parts[2:4])
                or not re.fullmatch(r'rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-'
                                    + re.escape(self.thread_id) + r'\.jsonl', parts[4])):
            _stop('PATH_UNSAFE')
        _physical(self.codex_home, budget)
        for parent in (home,) + tuple(path.parents)[:4]:
            st = budget.op(os.lstat, str(parent))
            if st.st_uid != self.uid or stat.S_IMODE(st.st_mode) & 0o077:
                _stop('PATH_UNSAFE')
        st = _physical(self.path, budget)
        if (not _private(st, self.mode, self.uid)
                or (st.st_dev, st.st_ino) != (self.dev, self.inode)):
            _stop('INPUT_IDENTITY_MISMATCH')
        return st


def result():
    return dict(schema_version=1, kind='NATIVE_CENSUS_STRUCTURE_ONLY',
        scope='BOUNDED_SAMPLE', status='BLOCKED', reasons=[], sample_clean=False,
        metadata_summary=dict(history_mode='UNKNOWN', history_presence=dict.fromkeys(HISTORY)),
        sample_bounds=dict(prefix_skipped=False, partial_first=False, partial_last=False),
        ordinal_summary=dict(origin='NOT_OBSERVED', contiguous=None, meta_plus_one=None),
        fixed_histograms={name: dict.fromkeys(keys, 0) for name, keys in (
            ('type', TYPES), ('event', EVENTS), ('item', ITEMS), ('phase', PHASES), ('error', ERRORS))},
        shape_counts=dict(user_text=0, agent_text=0, unsupported=0, unknown=0),
        identity_checks=dict(before=False, after=False),
        resource_counts=dict(bytes=0, reads=0, records=0), elapsed_ms=0,
        closed=False, snapshot_drained=False, code_pin={})


def _reason(out, code):
    if code not in CODES:
        code = 'PIN_INVALID'
    if code not in out['reasons']:
        out['reasons'].append(code)


def _finish(out, budget):
    codes = set(out['reasons'])
    out['status'] = ('BLOCKED' if codes - INCOMPLETE - UNSUPPORTED else
                     'UNSUPPORTED' if codes & UNSUPPORTED else
                     'INCOMPLETE' if codes else 'FORMAT_OBSERVED_SUPPORTED')
    out['sample_clean'] = (not codes or codes == {'BOUNDED_TAIL_SAMPLE'}) and (
        out['shape_counts']['unsupported'] == out['shape_counts']['unknown'] == 0
        and out['identity_checks']['after'] and out['closed']
        and out['ordinal_summary']['contiguous'] is True)
    out['resource_counts'].update(bytes=budget.bytes, reads=budget.reads)
    out['elapsed_ms'] = max(0, int((budget.clock() - budget.start) * 1000))
    return out


def _decode(raw, code):
    try:
        return _rollout_json(raw, LINE)
    except Exception:
        _stop(code)


def _ordinal(value):
    if type(value) is not int or not 0 <= value <= 2**64 - 1:
        _stop('ORDINAL_INVALID')
    return value


def _project(x, out):
    """No content/id values copied to output; histogram keys are all static."""
    hist = out['fixed_histograms']
    kind = x.get('type')
    key = kind if type(kind) is str and kind in TYPES[:-1] else 'UNKNOWN'
    hist['type'][key] += 1
    payload = x.get('payload')
    if type(payload) is not dict:
        _stop('INVALID_RECORD')
    def unknown():
        out['shape_counts']['unknown'] += 1
        _reason(out, 'UNKNOWN_TYPE')
    def unsupported():
        out['shape_counts']['unsupported'] += 1
        _reason(out, 'UNSUPPORTED_SHAPE')
    if key != 'event_msg':
        unknown()
        return
    event = payload.get('type')
    event = event if type(event) is str and event in EVENTS[:-1] else 'UNKNOWN'
    hist['event'][event] += 1
    if event == 'UNKNOWN':
        unknown()
    elif event == 'task_complete':
        error = ('ABSENT' if 'error' not in payload else 'NULL' if payload['error'] is None
                 else 'OBJECT' if type(payload['error']) is dict else 'OTHER')
        hist['error'][error] += 1
        if error != 'ABSENT':
            unsupported()
    elif event == 'item_completed':
        item = payload.get('item')
        if type(item) is not dict:
            unsupported()
            return
        item_type = item.get('type')
        item_type = item_type if type(item_type) is str and item_type in ITEMS[:-1] else 'UNKNOWN'
        hist['item'][item_type] += 1
        if item_type == 'UNKNOWN':
            unknown()
        elif item_type == 'Reasoning':
            pass  # No reasoning content is inspected or retained.
        else:
            content = item.get('content')
            if item_type == 'UserMessage':
                valid = type(content) is list and bool(content) and all(
                    type(v) is dict and v.get('type') == 'text' and type(v.get('text')) is str
                    and (not ('text_elements' in v) or type(v['text_elements']) is list) for v in content)
            else:
                valid = (type(content) is list and len(content) == 1
                         and type(content[0]) is dict and set(content[0]) == {'type', 'text'}
                         and content[0]['type'] == 'Text' and type(content[0]['text']) is str)
                phase = item.get('phase')
                phase = phase if type(phase) is str and phase in PHASES[:-1] else 'UNKNOWN'
                hist['phase'][phase] += 1
                if phase == 'UNKNOWN':
                    unsupported()
            if valid:
                out['shape_counts']['user_text' if item_type == 'UserMessage' else 'agent_text'] += 1
            else:
                unsupported()


def census(pin, *, _budget=None):
    """One metadata + bounded snapshot tail; no history correlation or seal.

    _budget is a private deterministic test seam, never a production CLI flag.
    """
    budget = _budget if _budget is not None else Budget()
    out = result()
    fd = None
    before = None
    try:
        if type(pin) is not InputPin:
            _stop('PIN_INVALID')
        before = pin.validate(budget)
        fd = budget.op(os.open, pin.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if _identity(budget.op(os.fstat, fd)) != _identity(before):
            _stop('INPUT_IDENTITY_MISMATCH')
        out['identity_checks']['before'] = True
        size = before.st_size
        # Read metadata one byte at a time would exhaust read-ops. A bounded
        # chunk can contain history bytes, but never parse/project them until
        # metadata admission succeeds. All bytes including sentinel are charged.
        head = b''
        while b'\n' not in head and len(head) <= LINE and len(head) < size:
            chunk = budget.read(fd, min(8192, LINE + 1 - len(head), size - len(head)))
            if not chunk:
                _stop('SOURCE_CHANGED')
            head += chunk
        line, sep, _ = head.partition(b'\n')
        if len(line) > LINE or (sep and len(line) + 1 > LINE):
            _stop('META_OVER_LIMIT')
        if not sep:
            _stop('META_OVER_LIMIT' if size > LINE else 'META_INVALID')
        x = _decode(line, 'META_INVALID')
        meta = x.get('payload')
        if (x.get('type') != 'session_meta' or type(meta) is not dict
                or type(x.get('timestamp')) is not str):
            _stop('META_INVALID')
        ordinal = _ordinal(x.get('ordinal'))
        if meta.get('id') != pin.thread_id or meta.get('session_id') != pin.session_id:
            _stop('IDENTITY_MISMATCH')
        out['metadata_summary']['history_presence'] = {k: k in meta for k in HISTORY}
        mode = meta.get('history_mode')
        out['metadata_summary']['history_mode'] = ('PAGINATED' if mode == 'paginated'
            else 'LEGACY' if mode == 'legacy' else 'UNKNOWN')
        if mode != 'paginated':
            _reason(out, 'LEGACY_HISTORY')
        if any(k in meta for k in HISTORY):
            _reason(out, 'FORK_OR_HISTORY_PRESENT')
        offset = len(line) + 1
        start = max(offset, size - min(TAIL, TOTAL - budget.bytes))
        skipped = start > offset
        out['sample_bounds']['prefix_skipped'] = skipped
        if skipped:
            _reason(out, 'BOUNDED_TAIL_SAMPLE')
        budget.op(os.lseek, fd, start, os.SEEK_SET)
        remaining = size - start
        pending = b''
        alignment = skipped
        previous = None
        stopped = False
        while remaining and not stopped:
            data = budget.read(fd, min(8192, remaining))
            if not data:
                _stop('SOURCE_CHANGED')
            remaining -= len(data)
            pending += data
            if alignment:
                _, lf, pending = pending.partition(b'\n')
                if not lf:
                    pending = b''
                    continue
                alignment = False
                out['sample_bounds']['partial_first'] = True
            while b'\n' in pending:
                raw, _, pending = pending.partition(b'\n')
                if out['resource_counts']['records'] >= RECORDS:
                    _reason(out, 'RECORD_LIMIT'); stopped = True; break
                if len(raw) + 1 > LINE:
                    _reason(out, 'LINE_OVER_LIMIT'); stopped = True; break
                budget.check()
                item = _decode(raw, 'INVALID_RECORD')
                current = _ordinal(item.get('ordinal'))
                if previous is None:
                    out['ordinal_summary']['origin'] = 'WINDOW_DERIVED' if skipped else 'META_SUCCESSOR'
                    if not skipped:
                        out['ordinal_summary']['meta_plus_one'] = current == ordinal + 1
                        if current != ordinal + 1:
                            _stop('ORDINAL_GAP')
                elif current != previous + 1:
                    _stop('ORDINAL_GAP')
                previous = current
                out['ordinal_summary']['contiguous'] = True
                out['resource_counts']['records'] += 1
                _project(item, out)
                budget.check()
            if len(pending) >= LINE:
                _reason(out, 'LINE_OVER_LIMIT'); stopped = True
        if alignment:
            _reason(out, 'TAIL_ALIGNMENT_UNAVAILABLE')
        elif pending and not stopped:
            out['sample_bounds']['partial_last'] = True
            _reason(out, 'PARTIAL_LAST_LINE')
        out['snapshot_drained'] = not remaining and not pending and not stopped
        budget.check()
    except Stop as exc:
        _reason(out, exc.code)
        if exc.code in ('ORDINAL_INVALID', 'ORDINAL_GAP'):
            out['ordinal_summary']['contiguous'] = False
    except (KeyboardInterrupt, SystemExit):
        _reason(out, 'INTERRUPTED')
    except Exception:
        _reason(out, 'INPUT_IDENTITY_MISMATCH')
    finally:
        if fd is not None:
            # Even a byte/read/record cap must not bypass final identity checks.
            try:
                end = budget.op(os.fstat, fd)
                path_end = _physical(pin.path, budget)
                if (_identity(end) != _identity(before)
                        or _identity(path_end) != _identity(before)
                        or end.st_size < before.st_size or path_end.st_size < before.st_size):
                    _stop('SOURCE_CHANGED')
                if end.st_size > before.st_size or path_end.st_size > before.st_size:
                    _reason(out, 'SOURCE_GREW')
                elif (end.st_mtime_ns != before.st_mtime_ns
                      or end.st_ctime_ns != before.st_ctime_ns):
                    _stop('SOURCE_CHANGED')
                out['identity_checks']['after'] = True
            except Stop as exc:
                _reason(out, exc.code)
            except BaseException:
                _reason(out, 'SOURCE_CHANGED')
            # Attempt owned fd close even on failure; never invent success.
            start_close = budget.clock()
            try:
                with _alarm(5):
                    os.close(fd)
                out['closed'] = True
                if budget.clock() - start_close >= 5:
                    _reason(out, 'CLOSE_FAILED')
            except BaseException:
                _reason(out, 'CLOSE_FAILED')
        try:
            budget.check()
        except Stop as exc:
            _reason(out, exc.code)
    return _finish(out, budget)


def _close_owned(fd, budget):
    # Unlike Budget.op, close must be attempted even after a deadline.
    start = budget.clock()
    try:
        with _alarm(5):
            os.close(fd)
    except Stop:
        raise
    except BaseException:
        _stop('CLOSE_FAILED')
    if budget.clock() - start >= 5:
        _stop('CLOSE_FAILED')
    budget.check()


def _hash_file(path, budget):
    st = _physical(path, budget)
    if not stat.S_ISREG(st.st_mode) or st.st_size > 4 * 1024 * 1024:
        _stop('RUNTIME_MISMATCH')
    fd = budget.op(os.open, str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if _identity(budget.op(os.fstat, fd)) != _identity(st):
            _stop('RUNTIME_MISMATCH')
        digest = hashlib.sha256()
        remaining = st.st_size
        while remaining:
            chunk = budget.op(os.read, fd, min(65536, remaining))
            if not chunk:
                _stop('RUNTIME_MISMATCH')
            digest.update(chunk); remaining -= len(chunk)
        end = budget.op(os.fstat, fd)
        if (_identity(end) != _identity(st) or end.st_size != st.st_size
                or end.st_mtime_ns != st.st_mtime_ns or end.st_ctime_ns != st.st_ctime_ns
                or _identity(_physical(path, budget)) != _identity(st)):
            _stop('RUNTIME_MISMATCH')
        return digest.hexdigest()
    finally:
        _close_owned(fd, budget)


def _read_pin(path, expected, budget):
    if type(expected) is not str or not re.fullmatch('[0-9a-f]{64}', expected):
        _stop('PIN_INVALID')
    st = _physical(path, budget)
    parent = budget.op(os.lstat, str(Path(path).parent))
    if (not _private(st, 0o600, os.getuid()) or st.st_size > 16384
            or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700):
        _stop('PIN_INVALID')
    fd = budget.op(os.open, path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if _identity(budget.op(os.fstat, fd)) != _identity(st):
            _stop('PIN_INVALID')
        data = budget.op(os.read, fd, 16385)
        end = budget.op(os.fstat, fd)
        if (len(data) != st.st_size or hashlib.sha256(data).hexdigest() != expected
                or _identity(end) != _identity(st) or end.st_mtime_ns != st.st_mtime_ns
                or end.st_ctime_ns != st.st_ctime_ns
                or _identity(_physical(path, budget)) != _identity(st)):
            _stop('PIN_INVALID')
        return _decode_pin(data)
    finally:
        _close_owned(fd, budget)


def _decode_pin(data):
    try:
        return _rollout_json(data, 16384)
    except Exception:
        _stop('PIN_INVALID')


def _validate_runtime(pin, root, budget, _runtime=None):
    if (set(pin) != {'kind', 'schema_version', 'root', 'source_sha', 'code_hashes',
                     'runtime', 'input', 'bounds'}
            or pin['kind'] != 'llmtool.native-step0-census-pin'
            or type(pin['schema_version']) is not int or pin['schema_version'] != 1
            or pin['root'] != str(root) or not re.fullmatch('[0-9a-f]{40}', pin['source_sha'])
            or pin['bounds'] != dict(line=LINE, total=TOTAL, tail=TAIL, records=RECORDS,
                                     reads=READS, operation_seconds=5, total_seconds=30)):
        _stop('PIN_INVALID')
    expected_python, expected_prefix = (_runtime if _runtime is not None else
        (str(root/'tools/llmtool/.venv/bin/python'), str(root/'tools/llmtool/.venv')))
    runtime = pin['runtime']
    if (set(runtime) != {'executable', 'prefix', 'version', 'interpreter_sha256'}
            or runtime['executable'] != sys.executable or sys.executable != expected_python
            or runtime['prefix'] != sys.prefix or sys.prefix != expected_prefix
            or runtime['version'] != list(sys.version_info[:3])
            or Path(__file__).absolute() != root/SOURCE_FILES['module']
            or Path(sys.modules[_rollout_json.__module__].__file__).absolute() != root/SOURCE_FILES['decoder']):
        _stop('RUNTIME_MISMATCH')
    hashes = pin['code_hashes']
    if type(hashes) is not dict or set(hashes) != set(SOURCE_FILES):
        _stop('PIN_INVALID')
    for name, rel in SOURCE_FILES.items():
        if not re.fullmatch('[0-9a-f]{64}', hashes[name]) or _hash_file(root/rel, budget) != hashes[name]:
            _stop('RUNTIME_MISMATCH')
    # venv executable normally symlinks outside the root; resolve only this
    # trusted runtime target, never an input or caller-specified hash target.
    if _hash_file(Path(sys.executable).resolve(), budget) != runtime['interpreter_sha256']:
        _stop('RUNTIME_MISMATCH')
    return InputPin(**pin['input'])


def _entry(argv, *, _root=None, _stdout=None, _runtime=None):
    """Private test-root injection, unavailable from argv/env/production main."""
    budget = Budget()
    out = result()
    output = sys.stdout if _stdout is None else _stdout
    handlers = {}
    def interrupted(signum, frame):
        _stop('INTERRUPTED')
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            handlers[sig] = signal.signal(sig, interrupted)
        if (len(argv) != 5 or argv[0] != '--pin-file' or argv[2] != '--pin-sha256'
                or argv[4] != '--json'):
            _stop('PIN_INVALID')
        actual_root = Path(__file__).absolute().parents[3]
        if _root is None and str(actual_root) not in (CANONICAL, CODEX):
            _stop('RUNTIME_MISMATCH')
        root = actual_root if _root is None else Path(_root)
        pin = _read_pin(argv[1], argv[3], budget)
        input_pin = _validate_runtime(pin, root, budget, _runtime)
        out = census(input_pin, _budget=budget)
        out['code_pin'] = dict(pin['code_hashes'], source_sha=pin['source_sha'])
        budget.check()
    except Stop as exc:
        _reason(out, exc.code)
    except (KeyboardInterrupt, SystemExit):
        _reason(out, 'INTERRUPTED')
    except Exception:
        _reason(out, 'PIN_INVALID')
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    _finish(out, budget)
    try:
        budget.check()
        encoded = json.dumps(out, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n'
        if len(encoded.encode('ascii')) > 16384:
            _stop('OUTPUT_FAILED')
        budget.check()
        output.write(encoded)
        output.flush()
        budget.check()
    except BaseException:
        # Cannot retract already emitted bytes. Nonzero actual exit takes
        # precedence; external caller must check exit as well as the JSON.
        return 1
    return EXITS[out['status']]


def main(argv=None):
    return _entry(sys.argv[1:] if argv is None else argv)
