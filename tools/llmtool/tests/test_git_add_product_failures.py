"""Native F1 product-domain failure acceptance (test-only harness).

Drives the real private launcher -> _git_add_host -> _git_supervisor -> git
chain in launcher-created disposable fixtures. Every fault is injected from
this test process (signals, SIGSTOP/SIGCONT, fixture drift) or from the
driver's own process (wrapping its Popen/Reader); no production fault flag.

Each scenario proves it reached the intended stage: the real `git add` child
is observed and frozen with SIGSTOP (so the fault lands after READY and the
supervisor's final gate), or the supervisor is frozen before it can gate.
"""
import ctypes
import array
import errno
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from llmtool import _git_process_identity as kernel
from test_git_add_private import PYTHON

SSTOP, SZOMB = 4, 5
BASELINE = {'target.txt': 'original', 'other.txt': 'other'}
CHANGES = {'target.txt': 'changed'}
_OWNED_RUNS = []  # Keep unresolved real Popen/FD responsibility, never GC/reap it.

DRIVER = r'''
import array, json, os, socket, subprocess, sys
from llmtool import _git_add_private as p, _git_process_identity as kernel
spec = json.loads(sys.argv[1])
observer = socket.socket(fileno=int(sys.argv[2]))
real_popen = subprocess.Popen
def out(tag, value):
    sys.stdout.write(tag + ' ' + json.dumps(value) + '\n')
    sys.stdout.flush()
class Popen(real_popen):
    def __init__(self, args, *a, **k):
        if 'llmtool._git_add_host' in args:
            out('ROOT', k['cwd'])
            if spec['drift']:
                with open(os.path.join(k['cwd'], 'repo', spec['drift']), 'ab') as f:
                    f.write(b'-drift')
        super().__init__(args, *a, **k)
        if 'llmtool._git_add_host' in args:
            # The launcher owns this unreaped Host. Keep that launch pin until
            # the outer test has registered cleanup, before sending the frame.
            out('HOST', kernel.identity(self.pid).data())
            # Duplicate the existing stdout reader for the outer test only.
            # The socket stays in the driver; close_fds gives the sealed Host
            # no new descriptor. This reader is idle until launcher loss.
            observer.sendmsg([b'H'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                      array.array('i', [self.stdout.fileno()]))])
            if sys.stdin.readline() != 'BOUND\n':
                raise RuntimeError('test owner did not bind Host')
subprocess.Popen = Popen
real_receive = p.Reader.receive
def receive(self, deadline):
    value = real_receive(self, deadline)
    out('FRAME', value)
    return value
p.Reader.receive = receive
encode = lambda d: {k: v.encode() for k, v in d.items()}
result = p.run_fixture_add(encode(spec['baseline']), encode(spec['changes']),
                           timeout=spec['timeout'])
out('RESULT', result)
'''


def _product_importable():
    return subprocess.run([PYTHON, '-I', '-c', 'import llmtool'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=30).returncode == 0


_libproc = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True) if sys.platform == 'darwin' else None
_libc = ctypes.CDLL(None, use_errno=True) if sys.platform == 'darwin' else None


def children(pid):
    rows = (ctypes.c_int * 1024)()
    ctypes.set_errno(0)
    count = _libproc.proc_listpids(6, pid, rows, ctypes.sizeof(rows))  # PROC_PPID_ONLY
    error = ctypes.get_errno()
    if error or count < 0 or count >= ctypes.sizeof(rows) or count % ctypes.sizeof(ctypes.c_int):
        raise kernel.IdentityUnknown('harness child inventory unavailable')
    return sorted(p for p in rows[:count//ctypes.sizeof(ctypes.c_int)] if p > 0)


def argv(pid):
    """Current argv via sysctl KERN_PROCARGS2; None if unreadable/gone."""
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
    size = ctypes.c_size_t(0)
    if _libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or not size.value:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if _libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return None
    raw = buf.raw[:size.value]
    argc, rest = int.from_bytes(raw[:4], sys.byteorder), raw[4:]
    rest = rest[rest.find(b'\0'):].lstrip(b'\0')  # skip exec path + padding
    return [a.decode(errors='replace') for a in rest.split(b'\0')[:argc]]


def is_supervisor(pid):
    args = argv(pid) or []
    return 'llmtool._git_supervisor' in args


def is_git_add(pid):
    args = argv(pid) or []
    return bool(args) and os.path.basename(args[0]) == 'git' and 'add' in args


def observed_start(pid):
    """Two kernel BSD rows for PID-only signals; never session/group authority.

    Unlike getsid(), the supported zombie-capable BSD query spans Darwin exec
    and exit. UID, parent and microsecond start must be stable across both rows.
    Full SID/PGID identity remains required for the original launch binding.
    """
    before = kernel._info(pid, 3, kernel._BSD)
    after = kernel._info(pid, 3, kernel._BSD)
    fields = ('pid', 'uid', 'ppid', 'seconds', 'micros')
    if before.pid != pid or any(getattr(before, k) != getattr(after, k) for k in fields):
        raise kernel.IdentityUnknown('harness kernel start changed')
    return after


@dataclass(frozen=True)
class StartIdentity:
    """Kernel identity sufficient only for ancestry-bound test PID signals."""
    pid: int
    uid: int
    ppid: int
    seconds: int
    micros: int

    def data(self):
        return asdict(self)


class OwnedProcess:
    """Test-only observed ancestry, kernel start identity and exit continuity.

    No group signals and no adoption of unobserved orphans. Exit notifications
    remain open across parent exit/reparenting and PID reuse. Like the product
    kernel snapshots, this assumes the closed trusted fixture, not a hostile
    same-UID reaper between the final check and kill(2).
    """
    def __init__(self, bound, parent, note, run=None):
        self.bound, self.parent, self.note = bound, parent, note
        self.pid = bound.pid
        self.run, self.stopped, self.retained = run, False, False
        self.role = None

    def current(self):
        if self.note.exited():
            return None
        try:
            value = observed_start(self.pid)
        except (OSError, kernel.IdentityUnknown):
            if self.note.exited():
                return None
            raise kernel.IdentityUnknown('harness identity unavailable') from None
        if any(getattr(value, key) != getattr(self.bound, key)
               for key in ('pid', 'uid', 'seconds', 'micros')):
            raise kernel.IdentityUnknown('harness identity reused')
        # A captured child can become an init-owned orphan after its captured
        # parent exits. Nothing else may replace that launch ancestry.
        if value.ppid != self.bound.ppid and not (
                value.ppid == 1 and self.parent is not None and self.parent.note.exited()):
            raise kernel.IdentityUnknown('harness ancestry changed')
        if self.note.exited():
            return None
        if getattr(value, 'status', None) == SZOMB:
            # Darwin NOTE_EXIT can trail its zombie BSD row. Wait only for the
            # registered original exit event, with no signal or reap.
            deadline = time.monotonic() + .05
            while time.monotonic() < deadline:
                if self.note.exited():
                    return None
                time.sleep(.0002)
            raise kernel.IdentityUnknown('harness exit notification unresolved')
        return value

    def _send_pinned(self, number):
        if self.current() is None:
            return False
        # This is the sole signal site. Never send to a remembered integer or
        # PGID; every STOP/CONT/fault/teardown uses this fresh identity check.
        try:
            os.kill(self.pid, number)
        except ProcessLookupError:
            if self.note.exited():
                return False
            raise kernel.IdentityUnknown('harness signal target unresolved') from None
        return True

    def orphan_pin(self):
        """Only fixed already-stopped Git or proven permanent operator-wait.

        These cannot naturally exit/reap in the closed fixture. No generic
        orphan, resumed child, or unexpected descendant earns this pin.
        """
        if self.retained and self.role == 'host':
            for pid in children(self.pid):
                child = self.run.owned.get(pid) if self.run is not None else None
                # C2 deliberately retains its unreaped supervisor zombie.
                # A matching, already-exited bound zombie is accounted for;
                # no live/new child earns orphan signalling authority.
                row = observed_start(pid)
                if (child is None or not child.note.exited() or row.status != SZOMB
                        or row.ppid != self.pid or any(getattr(row, key) != getattr(child.bound, key)
                            for key in ('pid', 'uid', 'seconds', 'micros'))):
                    raise kernel.IdentityUnknown('retained Host has unaccounted children')
            return
        if (self.role == 'git' and self.stopped and status(self) == SSTOP
                and not children(self.pid)):
            return
        raise kernel.IdentityUnknown('harness orphan signalling unsupported')

    @contextmanager
    def pin(self, number):
        if self.parent is None:
            if (self.run is None or self.run.driver.pid != self.pid
                    or self.run.driver.returncode is not None):
                raise kernel.IdentityUnknown('harness direct launch pin lost')
            yield  # This test retains the Popen; no poll/wait in this window.
            return
        if self.parent.current() is None:
            if number != signal.SIGKILL:
                raise kernel.IdentityUnknown('harness orphan accepts teardown only')
            self.orphan_pin()
            yield
            return
        # Stop the actual owner while *its* owner is pinned. It cannot reap
        # this child between our fresh check and signal. Preserve prior STOP.
        with self.parent.pin(signal.SIGSTOP):
            was_stopped = status(self.parent) == SSTOP
            if not was_stopped:
                if not _stop_pinned(self.parent):
                    raise kernel.IdentityUnknown('harness owner exited before pin')
            try:
                yield
            finally:
                if not was_stopped:
                    self.parent._send_pinned(signal.SIGCONT)

    def send(self, number):
        if self.current() is None:
            return False
        with self.pin(number):
            result = self._send_pinned(number)
        if number == signal.SIGCONT:
            self.stopped = False
        return result


class FixtureRun:
    """One launch owns its identities, descriptors and disposable root."""
    def __init__(self, driver, timeout):
        self.driver, self.timeout = driver, timeout
        self.buffer, self.lines = b'', {}
        self.owned, self.root, self.root_binding = {}, None, None
        self.launch = None
        self.host_bound, self.discovery_unknown = False, False
        self.control, self.host_status = None, None
        self.cleaned = False

    def bind(self, pid, parent=None, reported=None):
        if pid in self.owned:
            item = self.owned[pid]
            if item.current() is None:
                raise kernel.IdentityUnknown('harness original process exited')
            return item
        if parent is None or reported is not None:
            value = kernel.identity(pid)  # Exact launcher/Host transport binding.
        else:
            row = observed_start(pid)
            value = StartIdentity(**{key: getattr(row, key) for key in
                                     ('pid', 'uid', 'ppid', 'seconds', 'micros')})
        if value.uid != os.geteuid() or (parent is None and value.ppid != os.getpid()):
            raise kernel.IdentityUnknown('harness launch ownership unavailable')
        if parent is not None and (parent.current() is None or value.ppid != parent.pid):
            raise kernel.IdentityUnknown('harness child ownership unavailable')
        if reported is not None and value != reported:
            raise kernel.IdentityUnknown('harness Host binding changed')
        note = kernel.ExitNote(pid)
        item = OwnedProcess(value, parent, note, self)
        # Register responsibility before any next assertion, freeze or lookup.
        self.owned[pid] = item
        if item.current() is None:
            raise kernel.IdentityUnknown('harness process exited during binding')
        if reported is not None:
            self.host_bound = True
            item.role = 'host'
        return item

    def receive_status_reader(self):
        self.control.settimeout(10)
        data, ancillary, flags, _ = self.control.recvmsg(1, socket.CMSG_SPACE(array.array('i').itemsize))
        received = []
        try:
            for level, kind, raw in ancillary:
                if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS:
                    raise AssertionError('unexpected test observer message')
                values = array.array('i')
                values.frombytes(raw)
                received.extend(values)
            if data != b'H' or flags & socket.MSG_CTRUNC or len(received) != 1:
                raise AssertionError('test observer descriptor unavailable')
            os.set_inheritable(received[0], False)
            self.host_status = received.pop()
        finally:
            for fd in received:
                os.close(fd)

    def line(self, tag, seconds):
        deadline = time.monotonic() + seconds
        while tag not in self.lines:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if select.select([self.driver.stdout], [], [], remaining)[0]:
                chunk = os.read(self.driver.stdout.fileno(), 65536)
                if not chunk:
                    return None
                self.buffer += chunk
                if len(self.buffer) > 131072:
                    raise AssertionError('harness output limit')
                while b'\n' in self.buffer:
                    row, self.buffer = self.buffer.split(b'\n', 1)
                    name, _, value = row.decode().partition(' ')
                    self.lines.setdefault(name, json.loads(value))
        return self.lines[tag]

    def bind_root(self, value):
        if value is None:
            raise AssertionError('fixture root not observed')
        root = Path(value)
        row = root.lstat()
        if (not root.name.startswith('llmtool-f1-') or root != root.resolve()
                or root.parent != Path(tempfile.gettempdir()).resolve()
                or not stat.S_ISDIR(row.st_mode) or row.st_uid != os.geteuid()
                or row.st_mode & 0o077):
            raise AssertionError('unowned fixture root')
        self.root, self.root_binding = root, (row.st_dev, row.st_ino)

    def quiesce(self, item):
        if item.current() is None:
            return
        if item.parent is not None and item.parent.current() is None:
            item.orphan_pin()
            return
        if not wait_stopped(item):
            if item.current() is not None:
                raise kernel.IdentityUnknown('harness could not stop owner')
            return
        # Stop each owner before inventory, retaining the real driver Popen
        # without reap. Closed F1 git cannot create unobserved external helpers.
        for pid in children(item.pid):
            if pid in self.owned and self.owned[pid].current() is None:
                continue  # A tracked zombie remains reserved by its owner.
            child = self.bind(pid, item)
            self.quiesce(child)

    def cleanup(self):
        if self.cleaned:
            return
        # Failure/timeout/retry teardown is explicit, after retention checks.
        # An error preserves the fixture and launch pin; never report clean.
        if self.launch is None:
            raise kernel.IdentityUnknown('harness launch pin unresolved')
        if self.root is None:
            raise kernel.IdentityUnknown('harness fixture root unresolved')
        if self.discovery_unknown or (self.launch.current() is None and not self.host_bound):
            raise kernel.IdentityUnknown('harness launch descendants unresolved')
        self.quiesce(self.launch)
        # Also include previously registered orphans after their parent died.
        for item in list(self.owned.values()):
            self.quiesce(item)
        for item in reversed(list(self.owned.values())):
            item.send(signal.SIGKILL)
        for item in self.owned.values():
            if wait_absent(item, 5) is None:
                raise kernel.IdentityUnknown('harness cleanup process unresolved')
        self.driver.wait(timeout=5)
        for item in self.owned.values():
            if not wait_gone(item, 5):
                raise kernel.IdentityUnknown('harness cleanup original PID still present')
        self.driver.stdin.close()
        self.driver.stdout.close()
        if self.host_status is not None:
            os.close(self.host_status)
            self.host_status = None
        if self.control is not None:
            self.control.close()
        for item in self.owned.values():
            item.note.close()
        if self.root is not None:
            try:
                row = self.root.lstat()
            except FileNotFoundError:
                pass  # Successful product completion may already remove it.
            else:
                if ((row.st_dev, row.st_ino) != self.root_binding
                        or not stat.S_ISDIR(row.st_mode) or self.root != self.root.resolve()):
                    raise kernel.IdentityUnknown('harness fixture root replaced')
                shutil.rmtree(self.root)
        self.cleaned = True
        if self in _OWNED_RUNS:
            _OWNED_RUNS.remove(self)


def find_child(run, parent, match, seconds, freeze=False):
    """Find parent's child satisfying match. With freeze, every new child is
    SIGSTOPped on discovery and resumed unless it is the target, so even a
    short-lived target cannot exit between discovery and freeze."""
    checked, deadline = set(), time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not live(parent):
            return None
        for pid in children(parent.pid):
            if pid in checked:
                continue
            try:
                child = run.bind(pid, parent)
            except (OSError, kernel.IdentityUnknown):
                # A fast naturally exited child may disappear during binding.
                # It is never signalled. Failed lookup while still present is
                # uncertainty, not a successful inventory or a retry licence.
                if parent.current() is not None and pid not in children(parent.pid) and pid not in run.owned:
                    continue
                run.discovery_unknown = True
                raise
            if freeze and not child.send(signal.SIGSTOP):
                continue
            args = argv(pid)
            if args is not None and match(pid):
                child.role = 'git' if match is is_git_add else 'supervisor'
                return child
            if freeze:
                child.send(signal.SIGCONT)
            # A fork not yet exec'd still shows the parent's argv: recheck it.
            if args is not None and not any('llmtool._git_' in a for a in args):
                checked.add(pid)
    return None


def status(item):
    if item.current() is None:
        return None
    row = kernel._info(item.pid, 3, kernel._BSD)
    if any(getattr(row, key) != getattr(item.bound, key)
           for key in ('pid', 'uid', 'seconds', 'micros')):
        raise kernel.IdentityUnknown('harness status identity changed')
    return row.status


def live(item):
    return item.current() is not None


def wait_stopped(item, seconds=1):
    """Freeze pid until the kernel reports SSTOP. SIGSTOP is asynchronous and,
    on macOS, one landing while /usr/bin/git re-execs (shim -> xcrun -> git)
    can be lost, so it is re-sent until the stop is observed."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = status(item)
        if state == SSTOP:
            item.stopped = True
            return True
        if state in (None, SZOMB):
            return False
        if not item.send(signal.SIGSTOP):
            return False
        time.sleep(.0002)
    return False


def _stop_pinned(item, seconds=1):
    """An owner is already pinned by its ancestry; avoid recursive pinning."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = status(item)
        if state == SSTOP:
            return True
        if state in (None, SZOMB) or not item._send_pinned(signal.SIGSTOP):
            return False
        time.sleep(.0002)
    raise kernel.IdentityUnknown('harness owner stop deadline')


def wait_absent(item, seconds=2):
    """Original process exit; C2 may intentionally retain its zombie anchor."""
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        if item.note.exited():
            return time.monotonic() - start
        time.sleep(.002)
    return None


def wait_gone(item, seconds):
    """Teardown-only passive proof after all signals and launcher reap.

    NOTE_EXIT alone is not proof the original zombie disappeared. A reused PID
    is a different generation and is never signalled or waited on here.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not item.note.exited():
            return False
        try:
            row = observed_start(item.pid)
        except kernel.IdentityUnknown as error:
            if (getattr(error, 'libproc', {}).get('errno') == errno.ESRCH
                    and kernel.absent(item.pid)):
                return True
            raise
        if any(getattr(row, key) != getattr(item.bound, key)
               for key in ('pid', 'uid', 'seconds', 'micros')):
            return True
        time.sleep(.002)
    return False


def pipe_endpoint(pid, fd):
    """(own handle, peer handle) of a pipe FD via libproc PROC_PIDFDPIPEINFO.

    struct pipe_fdinfo = proc_fileinfo (24) + pipe_info: vinfo_stat (136),
    pipe_handle, pipe_peerhandle. A peer handle of 0 means no process anywhere
    still holds the other end, so a write to this end must fail with EPIPE.
    """
    buf = ctypes.create_string_buffer(184)
    size = _libproc.proc_pidfdinfo(pid, fd, 6, buf, ctypes.sizeof(buf))
    if size != ctypes.sizeof(buf):
        raise kernel.IdentityUnknown('harness pipe inventory unavailable')
    raw = buf.raw
    return (int.from_bytes(raw[160:168], sys.byteorder),
            int.from_bytes(raw[168:176], sys.byteorder))


def prove_structural_retention(host, supervisor, write_handle, seconds):
    """Retention proof when no status reader can receive the incident frame.

    Normal Host completion reaps its exited supervisor and exits within its
    own deadlines; only the _unknown path keeps that bound supervisor zombie
    unreaped while staying alive. Holding this for longer than every product
    deadline (run budget, 2 s handshake, 5 s cleanup) plus margin is the
    evidence; elapsed liveness alone never is. Grants teardown authority only.
    """
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        row = observed_start(supervisor.pid)
        if (not live(host) or not supervisor.note.exited() or row.status != SZOMB
                or row.ppid != host.pid or supervisor.pid not in children(host.pid)
                or any(getattr(row, key) != getattr(supervisor.bound, key)
                       for key in ('pid', 'uid', 'seconds', 'micros'))):
            raise AssertionError('Host did not retain its unreaped supervisor anchor')
        if pipe_endpoint(host.pid, 1) != (write_handle, 0):
            raise AssertionError('Host status pipe changed or regained a reader')
        time.sleep(.05)
    host.retained = True


@unittest.skipUnless(sys.platform == 'darwin' and os.geteuid() != 0, 'native Mac required')
class ProductFailureTests(unittest.TestCase):
    def setUp(self):
        if not _product_importable():
            self.skipTest('llmtool not installed for PYTHON -I driver')

    def start(self, drift=None, timeout=2):
        spec = {'baseline': BASELINE, 'changes': CHANGES, 'timeout': timeout, 'drift': drift}
        observer, sender = socket.socketpair()
        try:
            driver = subprocess.Popen([PYTHON, '-I', '-c', DRIVER, json.dumps(spec), str(sender.fileno())],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                close_fds=True, pass_fds=(sender.fileno(),))
        except BaseException:
            observer.close()
            raise
        finally:
            sender.close()
        run = FixtureRun(driver, timeout)
        run.control = observer
        _OWNED_RUNS.append(run)
        self.addCleanup(run.cleanup)
        run.launch = run.bind(driver.pid)
        run.bind_root(run.line('ROOT', 20))
        reported = kernel.Identity.parse(run.line('HOST', 10))
        host = run.bind(reported.pid, run.launch, reported)
        run.receive_status_reader()
        # Host is now tracked before further discovery/assertions. Release the
        # driver-owned unreaped launch anchor only after binding succeeded.
        driver.stdin.write(b'BOUND\n')
        driver.stdin.flush()
        return run, host

    def line(self, driver, tag, seconds):
        return driver.line(tag, seconds)

    def frozen_git(self, driver, host):
        """Freeze the real `git add` child; it is spawned only after READY and
        the supervisor's final gate, so this proves the fault lands there."""
        supervisor = find_child(driver, host, is_supervisor, 10)
        if supervisor is None:
            return None
        git = find_child(driver, supervisor, is_git_add, 10, freeze=True)
        if git is None or not wait_stopped(git):
            return None
        return supervisor, git

    def run_frozen(self, timeout=2):
        for _ in range(5):
            driver, host = self.start(timeout=timeout)
            frozen = self.frozen_git(driver, host)
            if frozen is not None:
                return driver, host, frozen[0], frozen[1]
            self.line(driver, 'RESULT', 20)
            driver.cleanup()  # A failed teardown raises; never retry it away.
        self.fail('real git child was never frozen after READY in 5 attempts')

    def assert_retained_unknown(self, frame):
        self.assertEqual(frame['outcome'], 'STATE_UNKNOWN')
        self.assertIs(frame['containment_confirmed'], False)
        self.assertEqual(frame['user_action'], 'USER_ACTION_REQUIRED')
        self.assertEqual(frame['code'], 'MUTATION_STATE_UNKNOWN')

    def observe_retained(self, run, host, git=None):
        # C2: observe beyond the entire run budget, not merely one second.
        margin = 1.0
        started = time.monotonic()
        while time.monotonic() - started < run.timeout + margin:
            self.assertTrue(live(host), 'Host released UNKNOWN instead of retaining it')
            if git is not None:
                self.assertEqual(status(git), SSTOP, 'orphaned git was touched or vanished')
            self.assertNotIn('RESULT', run.lines)
            self.line(run, 'RESULT', .01)
        self.assertGreaterEqual(time.monotonic() - started, run.timeout + margin)
        self.assertNotIn('RESULT', run.lines)

    def test_preflight_drift_before_host_refuses_without_supervisor_or_git(self):
        control, _ = self.start()
        self.assertEqual(self.line(control, 'RESULT', 20)['outcome'], 'APPLIED')
        driver, host = self.start(drift='target.txt')
        seen = set()
        deadline = time.monotonic() + 20
        while live(driver.launch) and 'RESULT' not in driver.lines:
            self.assertLess(time.monotonic(), deadline, 'preflight observation deadline')
            if live(host):
                seen.update(pid for pid in children(host.pid) if is_supervisor(pid))
            self.line(driver, 'RESULT', .001)
        result = self.line(driver, 'RESULT', 20)
        self.assertEqual(result['outcome'], 'AUTHORITY_REJECTED')
        self.assertIs(result['containment_confirmed'], True)
        self.assertEqual(set(result), {'outcome', 'containment_confirmed', 'fixture_provenance_digest'})
        self.assertEqual(seen, set(), "Host spawned a supervisor after drift")

    def test_supervisor_final_gate_refuses_post_handoff_drift_without_git(self):
        for _ in range(5):
            driver, host = self.start()
            supervisor = find_child(driver, host, is_supervisor, 10, freeze=True)
            self.assertIsNotNone(supervisor, 'supervisor not observed')
            # Frozen right after exec, before READY and its own preflight rerun.
            early = wait_stopped(supervisor) and not children(supervisor.pid)
            with open(driver.root/'repo'/'target.txt', 'ab') as f:
                f.write(b'-drift')
            supervisor.send(signal.SIGCONT)
            gits = set()
            deadline = time.monotonic() + 20
            while 'RESULT' not in driver.lines and live(driver.launch):
                self.assertLess(time.monotonic(), deadline, 'final gate observation deadline')
                if live(supervisor):
                    gits.update(pid for pid in children(supervisor.pid) if is_git_add(pid))
                self.line(driver, 'RESULT', .001)
            result = self.line(driver, 'RESULT', 20)
            if early:
                break
            driver.cleanup()
        self.assertTrue(early, 'supervisor never frozen before its final gate')
        self.assertEqual(gits, set(), 'git spawned despite final-gate drift')
        self.assertEqual(result['code'], 'MUTATION_START_FAILED')
        self.assertIs(result['containment_confirmed'], True)
        self.assertEqual(result['outcome'], 'NOT_APPLIED')
        self.assertIsNone(result['exit_code'])

    def host_signal_after_ready(self, number):
        driver, host, supervisor, git = self.run_frozen()
        self.assertTrue(host.send(number))
        frame = self.line(driver, 'FRAME', 10)
        self.assert_retained_unknown(frame)
        host.retained = True  # Actual _unknown incident, not elapsed liveness.
        # Supervisor's last act killed its whole group, frozen git included.
        self.assertIsNotNone(wait_absent(git), 'git survived cancellation')
        self.assertIsNotNone(wait_absent(supervisor), 'supervisor survived cancellation')
        self.observe_retained(driver, host)
        self.assertTrue(live(driver.launch), 'launcher returned from STATE_UNKNOWN')

    def test_host_sigterm_after_ready_is_retained_unknown(self):
        self.host_signal_after_ready(signal.SIGTERM)

    def test_host_sigint_after_ready_is_retained_unknown(self):
        self.host_signal_after_ready(signal.SIGINT)

    def test_launcher_loss_after_ready_cancels_and_host_retains(self):
        driver, host, supervisor, git = self.run_frozen()
        self.assertTrue(driver.launch.send(signal.SIGKILL))
        driver.driver.wait(timeout=5)
        # The launcher is gone; the test-only duplicate reads the real Host
        # incident. This is launcher-loss coverage, not an EPIPE assertion.
        from llmtool import _git_add_private as private
        frame = private.Reader(driver.host_status).receive(time.monotonic()+10)
        self.assert_retained_unknown(frame)
        host.retained = True
        self.assertIsNotNone(wait_absent(git, 5), 'git survived launcher loss')
        self.assertIsNotNone(wait_absent(supervisor, 5), 'supervisor survived launcher loss')
        self.observe_retained(driver, host)

    def test_last_status_reader_closed_after_ready_epipe_retains_unknown(self):
        """C2: the Host's incident report hits a real EPIPE and changes nothing."""
        driver, host, supervisor, git = self.run_frozen()
        write_handle, peer = pipe_endpoint(host.pid, 1)
        own_handle, own_peer = pipe_endpoint(os.getpid(), driver.host_status)
        # The duplicate is exactly the Host status pipe's read end.
        self.assertEqual((peer, own_peer), (own_handle, write_handle))
        os.close(driver.host_status)
        driver.host_status = None
        # The live launcher still reads; nothing has been reported yet.
        self.assertEqual(pipe_endpoint(host.pid, 1), (write_handle, own_handle))
        self.assertTrue(driver.launch.send(signal.SIGKILL))
        driver.driver.wait(timeout=5)
        # Kernel closes the launcher's descriptors before its exit is
        # observable; the Host reports only after observing that loss. So the
        # report is written with no reader left anywhere: a genuine EPIPE.
        self.assertEqual(pipe_endpoint(host.pid, 1), (write_handle, 0),
                         'a Host status reader still exists')
        self.assertIsNotNone(wait_absent(supervisor, 10), 'supervisor survived launcher loss')
        self.assertIsNotNone(wait_absent(git, 10), 'git survived launcher loss')
        prove_structural_retention(host, supervisor, write_handle,
                                   driver.timeout + 2 + 5 + 1.0)
        self.assertNotIn('RESULT', driver.lines)
        self.assertNotIn('FRAME', driver.lines)

    def test_structural_retention_rejects_reaped_or_released_anchor(self):
        driver, host, supervisor, git = self.run_frozen()
        write_handle, _ = pipe_endpoint(host.pid, 1)
        # Before any incident the supervisor is alive, not an unreaped zombie.
        with self.assertRaisesRegex(AssertionError, 'unreaped supervisor anchor'):
            prove_structural_retention(host, supervisor, write_handle, .2)
        self.assertFalse(host.retained)
        self.assertTrue(host.send(signal.SIGTERM))
        self.assert_retained_unknown(self.line(driver, 'FRAME', 10))
        host.retained = True  # Frame-proven; restores normal teardown authority.

    def test_supervisor_death_after_ready_retains_unknown_and_git(self):
        driver, host, supervisor, git = self.run_frozen()
        self.assertTrue(supervisor.send(signal.SIGKILL))
        frame = self.line(driver, 'FRAME', 10)
        self.assert_retained_unknown(frame)
        host.retained = True
        # Without proof of containment the Host neither signals the remembered
        # group nor claims the git child is gone: it stays for the operator.
        self.observe_retained(driver, host, git)
        self.assertTrue(live(driver.launch))

    def test_supervisor_timeout_after_ready_is_retained_unknown(self):
        driver, host, supervisor, git = self.run_frozen(timeout=1)
        started = time.monotonic()
        frame = self.line(driver, 'FRAME', 15)
        self.assert_retained_unknown(frame)
        host.retained = True
        self.assertGreaterEqual(time.monotonic() - started, .5)
        self.assertIsNotNone(wait_absent(git), 'git survived supervisor timeout')
        self.assertIsNotNone(wait_absent(supervisor), 'supervisor survived its timeout')
        self.observe_retained(driver, host)

    def test_failure_before_git_discovery_cleans_observed_host(self):
        observed = []
        original = FixtureRun.bind
        def fail_after_host(run, pid, parent=None, reported=None):
            item = original(run, pid, parent, reported)
            if reported is not None:
                observed.append((run, item))
                raise AssertionError('injected failure before git discovery')
            return item
        with patch.object(FixtureRun, 'bind', fail_after_host):
            with self.assertRaisesRegex(AssertionError, 'before git discovery'):
                self.start()
        self.assertEqual(len(observed), 1)
        run, host = observed[0]
        self.assertTrue(live(host))  # Driver holds its Host before the ACK.
        run.cleanup()
        self.assertTrue(run.cleaned)
        self.assertTrue(host.note.observed_exit)
        self.assertFalse(run.root.exists())


class HarnessSafetyTests(unittest.TestCase):
    """Direct negative paths use synthetic identities and intercepted signals.

    No real PID reuse or unknown process is signalled to prove rejection.
    Native product/cleanup scenarios remain separately gated above.
    """
    def setUp(self):
        self.bound = kernel.Identity(424242, 424242, 424242, os.geteuid(),
                                     os.getpid(), 123, 456)
        self.note = Mock()
        self.note.exited.return_value = False
        self.run = FixtureRun(Mock(pid=self.bound.pid, returncode=None), 2)
        self.run.root = Path('/synthetic/llmtool-f1-root')
        self.run.host_bound = True
        self.item = OwnedProcess(self.bound, None, self.note, self.run)
        self.run.launch, self.run.owned = self.item, {self.item.pid: self.item}
        self.real_observed_start = observed_start
        self.query = patch.object(kernel, 'identity', return_value=self.bound).start()
        patch(__name__+'.observed_start', side_effect=lambda pid: self.query(pid)).start()
        self.kill = patch.object(os, 'kill').start()
        self.addCleanup(patch.stopall)

    def test_reused_identity_never_signalled_for_any_fault_or_cleanup_signal(self):
        from dataclasses import replace
        self.query.return_value = replace(self.bound, micros=789)
        for number in (signal.SIGSTOP, signal.SIGCONT, signal.SIGTERM,
                       signal.SIGINT, signal.SIGKILL):
            with self.subTest(signal=number), self.assertRaises(kernel.IdentityUnknown):
                self.item.send(number)
        self.kill.assert_not_called()

    def test_identity_lookup_failure_never_signalled(self):
        for error in (kernel.IdentityUnknown('unavailable'), PermissionError()):
            self.query.side_effect = error
            with self.subTest(error=type(error)), self.assertRaises(kernel.IdentityUnknown):
                self.item.send(signal.SIGKILL)
        self.kill.assert_not_called()

    def test_exit_notification_prevents_signal_to_replacement_pid(self):
        self.note.exited.return_value = True
        self.assertFalse(self.item.send(signal.SIGSTOP))
        self.query.assert_not_called()
        self.kill.assert_not_called()

    def test_signal_lookup_failure_is_not_fabricated_absence(self):
        self.kill.side_effect = ProcessLookupError()
        with self.assertRaises(kernel.IdentityUnknown):
            self.item.send(signal.SIGKILL)

    def test_changed_ancestry_never_signalled(self):
        from dataclasses import replace
        self.query.return_value = replace(self.bound, ppid=12345)
        with self.assertRaises(kernel.IdentityUnknown):
            self.item.send(signal.SIGCONT)
        self.kill.assert_not_called()

    def test_child_not_owned_by_fixture_rejected_before_freeze(self):
        parent = OwnedProcess(self.bound, None, self.note)
        run = FixtureRun(Mock(), 2)
        from dataclasses import replace
        self.query.return_value = replace(self.bound, pid=424243, ppid=1)
        with self.assertRaises(kernel.IdentityUnknown), patch.object(kernel, 'ExitNote') as note:
            run.bind(424243, parent)
        note.assert_not_called()
        self.kill.assert_not_called()

    def test_binding_registers_cleanup_before_next_identity_failure(self):
        run = FixtureRun(Mock(), 2)
        self.query.side_effect = [self.bound, kernel.IdentityUnknown('failed after capture')]
        with patch.object(kernel, 'ExitNote', return_value=self.note):
            with self.assertRaises(kernel.IdentityUnknown):
                run.bind(self.bound.pid)
        self.assertIn(self.bound.pid, run.owned)
        self.note.close.assert_not_called()  # Kept with unresolved responsibility.
        self.kill.assert_not_called()

    def test_cleanup_uncertainty_fails_and_preserves_fixture(self):
        run = FixtureRun(Mock(), 2)
        run.launch, run.owned = self.item, {self.item.pid: self.item}
        run.root = Path('/unreachable/synthetic-fixture')
        self.query.side_effect = kernel.IdentityUnknown('unknown')
        with patch.object(shutil, 'rmtree') as remove:
            with self.assertRaises(kernel.IdentityUnknown):
                run.cleanup()
        remove.assert_not_called()
        self.kill.assert_not_called()
        run.driver.wait.assert_not_called()
        self.assertFalse(run.cleaned)

    def test_cleanup_missing_launch_pin_fails_without_kill_or_delete(self):
        run = FixtureRun(Mock(), 2)
        with patch.object(shutil, 'rmtree') as remove:
            with self.assertRaises(kernel.IdentityUnknown):
                run.cleanup()
        remove.assert_not_called()
        self.kill.assert_not_called()

    def test_retained_host_registered_before_git_discovery_is_cleaned(self):
        # Actual cleanup method, synthetic retained Host. The first two
        # quiescence calls leave the captured processes stopped; SIGKILL then
        # produces per-identity exit notes. No Git registration is required.
        from dataclasses import replace
        run = self.run
        host_note = Mock()
        host_note.exited.return_value = False
        host_bound = replace(self.bound, pid=424243, ppid=self.bound.pid)
        host = OwnedProcess(host_bound, self.item, host_note, run)
        run.launch = self.item
        run.owned = {self.item.pid: self.item, host.pid: host}
        self.query.side_effect = lambda pid: self.bound if pid == self.item.pid else host_bound
        def killed(pid, number):
            if number == signal.SIGKILL:
                (self.note if pid == self.item.pid else host_note).exited.return_value = True
        self.kill.side_effect = killed
        with patch.object(run, 'quiesce'), patch(__name__+'.status', return_value=SSTOP), \
                patch(__name__+'.wait_gone', return_value=True), \
                patch.object(shutil, 'rmtree') as remove:
            run.cleanup()
            count = self.kill.call_count
            run.cleanup()
        self.assertTrue(run.cleaned)
        self.assertIn(unittest.mock.call(host.pid, signal.SIGKILL), self.kill.call_args_list)
        self.assertEqual(self.kill.call_count, count)  # Idempotent teardown.
        remove.assert_not_called()
        host_note.close.assert_called_once()

    def test_retry_does_not_hide_cleanup_uncertainty(self):
        case = ProductFailureTests('test_supervisor_timeout_after_ready_is_retained_unknown')
        run = Mock()
        run.cleanup.side_effect = kernel.IdentityUnknown('cleanup unresolved')
        with patch.object(case, 'start', return_value=(run, self.item)) as start, \
                patch.object(case, 'frozen_git', return_value=None), patch.object(case, 'line'):
            with self.assertRaises(kernel.IdentityUnknown):
                case.run_frozen()
        start.assert_called_once()

    def test_cleanup_exit_wait_failure_preserves_fixture_and_pin(self):
        run = self.run
        run.launch, run.owned = self.item, {self.item.pid: self.item}
        with patch.object(run, 'quiesce'), patch(__name__+'.wait_absent', return_value=None), \
                patch.object(shutil, 'rmtree') as remove:
            with self.assertRaises(kernel.IdentityUnknown):
                run.cleanup()
        remove.assert_not_called()
        run.driver.wait.assert_not_called()
        self.note.close.assert_not_called()

    def test_frozen_discovery_registers_before_stop_and_keeps_early_failure(self):
        run = FixtureRun(Mock(), 2)
        run.launch = self.item
        from dataclasses import replace
        child_bound = replace(self.bound, pid=424243, ppid=self.item.pid)
        self.query.side_effect = lambda pid: self.bound if pid == self.item.pid else child_bound
        notes = {self.item.pid: self.note, child_bound.pid: Mock()}
        notes[child_bound.pid].exited.return_value = False
        def stopped(pid, number):
            self.assertIn(pid, run.owned)
            raise AssertionError('injected stop failure')
        self.kill.side_effect = stopped
        with patch(__name__+'.children', return_value=[child_bound.pid]), \
                patch(__name__+'.status', return_value=SSTOP), \
                patch.object(kernel, 'ExitNote', return_value=notes[child_bound.pid]):
            with self.assertRaisesRegex(AssertionError, 'injected stop failure'):
                find_child(run, self.item, lambda pid: True, .1, freeze=True)
        self.assertIn(child_bound.pid, run.owned)

    def test_runs_never_share_root_or_cleanup_inventory(self):
        one, two = FixtureRun(Mock(), 1), FixtureRun(Mock(), 2)
        one.owned[self.item.pid] = self.item
        one.root = Path('/synthetic/one')
        self.assertEqual(two.owned, {})
        self.assertIsNone(two.root)

    def test_inventory_zero_with_errno_is_not_successful_empty(self):
        def error(*args):
            ctypes.set_errno(12)
            return 0
        with patch(__name__+'._libproc') as lib:
            lib.proc_listpids.side_effect = error
            with self.assertRaises(kernel.IdentityUnknown):
                children(self.item.pid)
        self.kill.assert_not_called()

    def test_exited_launcher_without_host_registration_blocks_delete(self):
        self.run.host_bound = False
        self.note.exited.return_value = True
        with patch.object(shutil, 'rmtree') as remove:
            with self.assertRaisesRegex(kernel.IdentityUnknown, 'descendants unresolved'):
                self.run.cleanup()
        self.kill.assert_not_called()
        remove.assert_not_called()
        self.assertFalse(self.run.cleaned)

    def test_unknown_root_blocks_cleanup_success(self):
        self.run.root = None
        with self.assertRaisesRegex(kernel.IdentityUnknown, 'root unresolved'):
            self.run.cleanup()
        self.kill.assert_not_called()
        self.assertFalse(self.run.cleaned)

    def test_exit_during_identity_lookup_prevents_signal(self):
        def exited(pid):
            self.note.exited.return_value = True
            return self.bound
        self.query.side_effect = exited
        self.assertFalse(self.item.send(signal.SIGSTOP))
        self.kill.assert_not_called()

    def test_generic_orphan_never_signalled(self):
        from dataclasses import replace
        self.note.exited.return_value = True
        child_note = Mock()
        child_note.exited.return_value = False
        bound = replace(self.bound, pid=424243, ppid=self.item.pid)
        item = OwnedProcess(bound, self.item, child_note, self.run)
        self.query.return_value = replace(bound, ppid=1)
        with self.assertRaisesRegex(kernel.IdentityUnknown, 'orphan signalling unsupported'), \
                patch(__name__+'.children', return_value=[]):
            item.send(signal.SIGKILL)
        self.kill.assert_not_called()

    def test_child_signal_holds_owner_stopped_until_signal_and_resumes(self):
        from dataclasses import replace
        child_note = Mock()
        child_note.exited.return_value = False
        bound = replace(self.bound, pid=424243, ppid=self.item.pid)
        child = OwnedProcess(bound, self.item, child_note, self.run)
        self.query.side_effect = lambda pid: self.bound if pid == self.item.pid else bound
        state = {'parent_stopped': False}
        def send(pid, number):
            if pid == self.item.pid:
                state['parent_stopped'] = number == signal.SIGSTOP
            else:
                self.assertTrue(state['parent_stopped'])
        self.kill.side_effect = send
        def stopped(item):
            self.item._send_pinned(signal.SIGSTOP)
            return True
        with patch(__name__+'.status', return_value=2), patch(__name__+'._stop_pinned', stopped):
            self.assertTrue(child.send(signal.SIGKILL))
        self.assertEqual(self.kill.call_args_list,
            [unittest.mock.call(self.item.pid, signal.SIGSTOP),
             unittest.mock.call(child.pid, signal.SIGKILL),
             unittest.mock.call(self.item.pid, signal.SIGCONT)])
        self.assertFalse(state['parent_stopped'])

    def test_parent_exit_during_pin_never_resumes_orphan(self):
        from dataclasses import replace
        note = Mock()
        note.exited.return_value = False
        bound = replace(self.bound, pid=424243, ppid=self.item.pid)
        child = OwnedProcess(bound, self.item, note, self.run)
        child.role, child.stopped = 'git', True
        def parent_exited(pid):
            if pid == bound.pid:
                self.note.exited.return_value = True
                return replace(bound, ppid=1)
            return self.bound
        self.query.side_effect = parent_exited
        with patch(__name__+'.status', return_value=SSTOP), \
                patch(__name__+'.children', return_value=[]):
            with self.assertRaisesRegex(kernel.IdentityUnknown, 'teardown only'):
                child.send(signal.SIGCONT)
        self.kill.assert_not_called()
        self.assertTrue(child.stopped)

    def test_elapsed_observation_never_grants_unknown_host_signal_authority(self):
        case = ProductFailureTests('test_launcher_loss_after_ready_cancels_and_host_retains')
        with patch.object(time, 'monotonic', side_effect=[0, 3, 3]):
            case.observe_retained(self.run, self.item)
        self.assertFalse(self.item.retained)
        self.assertNotIn('FRAME', self.run.lines)

    def test_stopped_unclassified_orphan_is_not_owned_git(self):
        self.item.stopped = True
        with patch(__name__+'.status', return_value=SSTOP), \
                patch(__name__+'.children', return_value=[]):
            with self.assertRaisesRegex(kernel.IdentityUnknown, 'unsupported'):
                self.item.orphan_pin()

    def test_status_reader_bad_message_closes_all_received_descriptors(self):
        self.run.control = Mock()
        self.run.control.recvmsg.return_value = (
            b'X', [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                    array.array('i', [424244, 424245]).tobytes())], socket.MSG_CTRUNC, None)
        with patch.object(os, 'close') as close:
            with self.assertRaisesRegex(AssertionError, 'descriptor unavailable'):
                self.run.receive_status_reader()
        self.assertEqual(close.call_args_list,
                         [unittest.mock.call(424244), unittest.mock.call(424245)])
        self.assertIsNone(self.run.host_status)

    def test_status_reader_is_noninheritable_and_closed_after_owned_exit(self):
        self.run.control = Mock()
        self.run.control.recvmsg.return_value = (
            b'H', [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                    array.array('i', [424244]).tobytes())], 0, None)
        with patch.object(os, 'set_inheritable') as inheritance, patch.object(os, 'close') as close:
            self.run.receive_status_reader()
            self.note.exited.return_value = True
            with patch.object(self.run, 'quiesce'), patch(__name__+'.wait_gone', return_value=True):
                self.run.cleanup()
        inheritance.assert_called_once_with(424244, False)
        close.assert_called_once_with(424244)
        self.run.control.close.assert_called_once()
        self.assertIsNone(self.run.host_status)

    def test_bsd_start_query_rejects_two_rows_with_changed_start(self):
        before, after = kernel._BSD(), kernel._BSD()
        for row in (before, after):
            for key in ('pid', 'uid', 'ppid', 'seconds', 'micros'):
                setattr(row, key, getattr(self.bound, key))
        after.micros += 1
        with patch.object(kernel, '_info', side_effect=[before, after]):
            with self.assertRaisesRegex(kernel.IdentityUnknown, 'start changed'):
                self.real_observed_start(self.bound.pid)
        self.kill.assert_not_called()

    def test_bsd_start_query_read_failure_is_never_usable_identity(self):
        with patch.object(kernel, '_info', side_effect=kernel.IdentityUnknown('read failed')):
            with self.assertRaises(kernel.IdentityUnknown):
                self.real_observed_start(self.bound.pid)
        self.kill.assert_not_called()

    def test_original_zombie_waits_for_exit_note_without_signal(self):
        from types import SimpleNamespace
        self.query.return_value = SimpleNamespace(**self.bound.data(), status=SZOMB)
        self.note.exited.side_effect = [False, False, True]
        self.assertFalse(self.item.send(signal.SIGKILL))
        self.kill.assert_not_called()

    def test_retained_host_accepts_only_bound_exited_supervisor_zombie(self):
        from dataclasses import replace
        from types import SimpleNamespace
        bound = replace(self.bound, pid=424243, ppid=self.item.pid)
        note = Mock()
        note.exited.return_value = True
        child = OwnedProcess(bound, self.item, note, self.run)
        self.run.owned[child.pid] = child
        self.item.role, self.item.retained = 'host', True
        self.query.return_value = SimpleNamespace(**bound.data(), status=SZOMB)
        with patch(__name__+'.children', return_value=[child.pid]):
            self.item.orphan_pin()
            self.query.return_value.status = 2
            with self.assertRaisesRegex(kernel.IdentityUnknown, 'unaccounted children'):
                self.item.orphan_pin()
        self.kill.assert_not_called()

    def test_teardown_original_absence_uncertainty_preserves_fixture(self):
        self.note.exited.return_value = True
        with patch.object(self.run, 'quiesce'), patch(__name__+'.wait_gone', return_value=False), \
                patch.object(shutil, 'rmtree') as remove:
            with self.assertRaisesRegex(kernel.IdentityUnknown, 'PID still present'):
                self.run.cleanup()
        remove.assert_not_called()
        self.assertFalse(self.run.cleaned)
        self.note.close.assert_not_called()

    def test_child_exec_session_transition_does_not_weaken_start_binding(self):
        from dataclasses import replace
        child_bound = replace(self.bound, pid=424243, ppid=self.item.pid)
        note = Mock()
        note.exited.return_value = False
        with patch(__name__+'.observed_start', side_effect=lambda pid:
                   self.bound if pid == self.item.pid else child_bound), \
                patch.object(kernel, 'identity', side_effect=kernel.IdentityUnknown('session transition')) as full, \
                patch.object(kernel, 'ExitNote', return_value=note):
            child = self.run.bind(child_bound.pid, self.item)
        self.assertIsInstance(child.bound, StartIdentity)
        self.assertEqual(child.bound.micros, child_bound.micros)
        full.assert_not_called()
        self.kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
