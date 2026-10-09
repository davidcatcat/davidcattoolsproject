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
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import time
import unittest

from llmtool import _git_process_identity as kernel
from test_git_add_private import PYTHON

SSTOP, SZOMB = 4, 5
BASELINE = {'target.txt': 'original', 'other.txt': 'other'}
CHANGES = {'target.txt': 'changed'}

DRIVER = r'''
import json, os, subprocess, sys
from llmtool import _git_add_private as p
spec = json.loads(sys.argv[1])
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
    count = _libproc.proc_listpids(6, pid, rows, ctypes.sizeof(rows))  # PROC_PPID_ONLY
    return sorted(p for p in rows[:max(count, 0)//ctypes.sizeof(ctypes.c_int)] if p > 0)


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


def find_child(parent, match, seconds, freeze=False):
    """Find parent's child satisfying match. With freeze, every new child is
    SIGSTOPped on discovery and resumed unless it is the target, so even a
    short-lived target cannot exit between discovery and freeze."""
    checked, deadline = set(), time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not live(parent):
            return None
        for pid in children(parent):
            if pid in checked:
                continue
            if freeze:
                try:
                    os.kill(pid, signal.SIGSTOP)
                except ProcessLookupError:
                    checked.add(pid)
                    continue
            args = argv(pid)
            if args is not None and match(pid):
                return pid
            if freeze:
                try:
                    os.kill(pid, signal.SIGCONT)
                except ProcessLookupError:
                    pass
            # A fork not yet exec'd still shows the parent's argv: recheck it.
            if args is not None and not any('llmtool._git_' in a for a in args):
                checked.add(pid)
    return None


def status(pid):
    try:
        return kernel._info(pid, 3, kernel._BSD).status
    except (OSError, kernel.IdentityUnknown):
        return None


def live(pid):
    return status(pid) not in (None, SZOMB)


def wait_stopped(pid, seconds=1):
    """Freeze pid until the kernel reports SSTOP. SIGSTOP is asynchronous and,
    on macOS, one landing while /usr/bin/git re-execs (shim -> xcrun -> git)
    can be lost, so it is re-sent until the stop is observed."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = status(pid)
        if state == SSTOP:
            return True
        if state in (None, SZOMB):
            return False
        try:
            os.kill(pid, signal.SIGSTOP)
        except ProcessLookupError:
            return False
        time.sleep(.0002)
    return False


def wait_absent(pid, seconds=2):
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        if not live(pid):
            return time.monotonic() - start
        time.sleep(.002)
    return None


@unittest.skipUnless(sys.platform == 'darwin' and os.geteuid() != 0, 'native Mac required')
class ProductFailureTests(unittest.TestCase):
    def setUp(self):
        if not _product_importable():
            self.skipTest('llmtool not installed for PYTHON -I driver')
        self.killed = []

    def start(self, drift=None, timeout=2):
        spec = {'baseline': BASELINE, 'changes': CHANGES, 'timeout': timeout, 'drift': drift}
        driver = subprocess.Popen([PYTHON, '-I', '-c', DRIVER, json.dumps(spec)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            close_fds=True)
        self.addCleanup(self.cleanup, driver)
        self.buffer, self.lines = b'', {}
        self.root = Path(self.line(driver, 'ROOT', 20))
        host = find_child(driver.pid, lambda pid: 'llmtool._git_add_host' in (argv(pid) or []), 10)
        self.assertIsNotNone(host, 'Host process not observed')
        return driver, host

    def cleanup(self, driver):
        for pid in reversed(self.killed):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if driver.poll() is None:
            driver.kill()
        driver.wait(timeout=5)
        driver.stdout.close()
        for pid in self.killed:
            self.assertIsNotNone(wait_absent(pid, 5), 'harness cleanup left pid %d' % pid)
        if getattr(self, 'root', None) is not None and self.root.name.startswith('llmtool-f1-'):
            shutil.rmtree(self.root, ignore_errors=True)

    def line(self, driver, tag, seconds):
        deadline = time.monotonic() + seconds
        while tag not in self.lines:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if select.select([driver.stdout], [], [], remaining)[0]:
                chunk = os.read(driver.stdout.fileno(), 65536)
                if not chunk:
                    return None
                self.buffer += chunk
                while b'\n' in self.buffer:
                    row, self.buffer = self.buffer.split(b'\n', 1)
                    name, _, value = row.decode().partition(' ')
                    self.lines.setdefault(name, json.loads(value))
        return self.lines[tag]

    def frozen_git(self, driver, host):
        """Freeze the real `git add` child; it is spawned only after READY and
        the supervisor's final gate, so this proves the fault lands there."""
        supervisor = find_child(host, is_supervisor, 10)
        if supervisor is None:
            return None
        git = find_child(supervisor, is_git_add, 10, freeze=True)
        if git is None or not wait_stopped(git):
            return None
        self.killed.extend((host, supervisor, git))
        return supervisor, git

    def run_frozen(self, timeout=2):
        for _ in range(5):
            driver, host = self.start(timeout=timeout)
            frozen = self.frozen_git(driver, host)
            if frozen is not None:
                return driver, host, frozen[0], frozen[1]
            self.line(driver, 'RESULT', 20)
            self.doCleanups()
            self.killed = []
        self.fail('real git child was never frozen after READY in 5 attempts')

    def assert_retained_unknown(self, frame):
        self.assertEqual(frame['outcome'], 'STATE_UNKNOWN')
        self.assertIs(frame['containment_confirmed'], False)
        self.assertEqual(frame['user_action'], 'USER_ACTION_REQUIRED')
        self.assertEqual(frame['code'], 'MUTATION_STATE_UNKNOWN')

    def test_preflight_drift_before_host_refuses_without_supervisor_or_git(self):
        control, _ = self.start()
        self.assertEqual(self.line(control, 'RESULT', 20)['outcome'], 'APPLIED')
        driver, host = self.start(drift='target.txt')
        seen = set()
        while driver.poll() is None and 'RESULT' not in self.lines:
            seen.update(pid for pid in children(host) if is_supervisor(pid))
            self.line(driver, 'RESULT', .001)
        result = self.line(driver, 'RESULT', 20)
        self.assertEqual(result['outcome'], 'AUTHORITY_REJECTED')
        self.assertIs(result['containment_confirmed'], True)
        self.assertEqual(set(result), {'outcome', 'containment_confirmed', 'fixture_provenance_digest'})
        self.assertEqual(seen, set(), "Host spawned a supervisor after drift")

    def test_supervisor_final_gate_refuses_post_handoff_drift_without_git(self):
        for _ in range(5):
            driver, host = self.start()
            supervisor = find_child(host, is_supervisor, 10, freeze=True)
            self.assertIsNotNone(supervisor, 'supervisor not observed')
            # Frozen right after exec, before READY and its own preflight rerun.
            early = wait_stopped(supervisor) and not children(supervisor)
            with open(self.root/'repo'/'target.txt', 'ab') as f:
                f.write(b'-drift')
            os.kill(supervisor, signal.SIGCONT)
            gits = set()
            while 'RESULT' not in self.lines and driver.poll() is None:
                if live(supervisor):
                    gits.update(pid for pid in children(supervisor) if is_git_add(pid))
                self.line(driver, 'RESULT', .001)
            result = self.line(driver, 'RESULT', 20)
            if early:
                break
            self.doCleanups()
        self.assertTrue(early, 'supervisor never frozen before its final gate')
        self.assertEqual(gits, set(), 'git spawned despite final-gate drift')
        self.assertEqual(result['code'], 'MUTATION_START_FAILED')
        self.assertIs(result['containment_confirmed'], True)
        self.assertEqual(result['outcome'], 'NOT_APPLIED')
        self.assertIsNone(result['exit_code'])

    def host_signal_after_ready(self, number):
        driver, host, supervisor, git = self.run_frozen()
        os.kill(host, number)
        frame = self.line(driver, 'FRAME', 10)
        self.assert_retained_unknown(frame)
        # Supervisor's last act killed its whole group, frozen git included.
        self.assertIsNotNone(wait_absent(git), 'git survived cancellation')
        self.assertIsNotNone(wait_absent(supervisor), 'supervisor survived cancellation')
        time.sleep(1)
        self.assertTrue(live(host), 'Host released UNKNOWN instead of retaining it')
        self.assertIsNone(driver.poll(), 'launcher returned from STATE_UNKNOWN')
        self.assertNotIn('RESULT', self.lines)

    def test_host_sigterm_after_ready_is_retained_unknown(self):
        self.host_signal_after_ready(signal.SIGTERM)

    def test_host_sigint_after_ready_is_retained_unknown(self):
        self.host_signal_after_ready(signal.SIGINT)

    def test_launcher_loss_after_ready_cancels_and_host_retains(self):
        driver, host, supervisor, git = self.run_frozen()
        driver.kill()
        driver.wait(timeout=5)
        # Host's status reader is gone; its UNKNOWN report fails silently,
        # which must not release retained responsibility.
        self.assertIsNotNone(wait_absent(git, 5), 'git survived launcher loss')
        self.assertIsNotNone(wait_absent(supervisor, 5), 'supervisor survived launcher loss')
        time.sleep(1)
        self.assertTrue(live(host), 'Host exited after launcher loss instead of retaining')

    def test_supervisor_death_after_ready_retains_unknown_and_git(self):
        driver, host, supervisor, git = self.run_frozen()
        os.kill(supervisor, signal.SIGKILL)
        frame = self.line(driver, 'FRAME', 10)
        self.assert_retained_unknown(frame)
        time.sleep(1)
        # Without proof of containment the Host neither signals the remembered
        # group nor claims the git child is gone: it stays for the operator.
        self.assertEqual(status(git), SSTOP, 'orphaned git was touched or vanished')
        self.assertTrue(live(host), 'Host released UNKNOWN instead of retaining it')
        self.assertIsNone(driver.poll())

    def test_supervisor_timeout_after_ready_is_retained_unknown(self):
        driver, host, supervisor, git = self.run_frozen(timeout=1)
        started = time.monotonic()
        frame = self.line(driver, 'FRAME', 15)
        self.assert_retained_unknown(frame)
        self.assertGreaterEqual(time.monotonic() - started, .5)
        self.assertIsNotNone(wait_absent(git), 'git survived supervisor timeout')
        self.assertIsNotNone(wait_absent(supervisor), 'supervisor survived its timeout')
        time.sleep(1)
        self.assertTrue(live(host), 'Host released UNKNOWN instead of retaining it')


if __name__ == '__main__':
    unittest.main()
