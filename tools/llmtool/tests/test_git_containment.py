"""Deterministic unit coverage plus explicitly invoked actual-Mac scenarios.

unittest discovery runs the unit classes. The outer runner separately invokes
AcceptanceScenarios via run_containment_acceptance.py; mocks never satisfy its
native signal, process, kqueue or SIGKILL repetitions.
"""
import ast
import ctypes
from contextlib import contextmanager
from dataclasses import replace
import errno
import hashlib
import importlib
import os
from pathlib import Path
import runpy
import select
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from llmtool import _git_containment as containment
from llmtool import _git_containment_protocol as wire
from llmtool import _git_process_identity as kernel

PACKAGE = Path(wire.__file__).resolve().parent
HOST = PACKAGE.parent / 'tests/containment_probe_parent.py'
NONCE = 'a' * 64
_FAILED_PROBES = []  # Retain unreaped ownership for external resolution.
_LAST_HEARTBEAT = time.monotonic()


def heartbeat():
    # Synchronous progress only: no thread, timer callback or repeated command.
    global _LAST_HEARTBEAT
    now = time.monotonic()
    if now-_LAST_HEARTBEAT >= 20:
        print('[Running]', flush=True)
        _LAST_HEARTBEAT = now


def retained_processes():
    """Synthetic identity evidence only; a snapshot never authorizes a signal."""
    return [probe.inventory() if isinstance(probe, Probe) else {'pid': probe.process.pid,
             'host_identity': probe.host_identity.data() if probe.host_identity else {},
             'supervisor': probe.supervisor.data() if probe.supervisor else {}}
            for probe in _FAILED_PROBES]


def cleanup_preserving_failure(cleanup):
    """Native assertion backstops keep a pending exception, never raw text."""
    primary = sys.exc_info()[1]
    if primary is not None and not hasattr(primary, 'primary_before_cleanup'):
        primary.primary_before_cleanup = wire.exception_evidence(primary)
    try:
        result = cleanup()
        if primary is not None and type(result) is dict and 'original_owned_processes' in result:
            primary.fixture_cleanup = result
        return result
    except BaseException as secondary:
        if getattr(secondary, 'fixture_cleanup', None) is not None and primary is not None:
            primary.fixture_cleanup = secondary.fixture_cleanup
        if primary is None:
            raise
        faults = getattr(primary, 'secondary_cleanup_errors', [])
        primary.secondary_cleanup_errors = (faults + [wire.exception_evidence(secondary)])[-4:]


def cleanup_fixture(process, bound):
    """Assertion backstop for direct unit/probe-owned session leaders."""
    original = {'pid': process.pid, 'host_identity': bound.data() if bound else {}, 'supervisor': {}}
    proof = {'host_pin_verified': False, 'decision_before_reap': False,
             'host_reaped': False, 'host_absent': False, 'group_absent': False}
    cleanup = {'status': 'UNRESOLVED', 'original_owned_processes': [original], 'evidence': proof}
    try:
        if bound is None:
            raise kernel.IdentityUnknown('fixture launch identity unavailable')
        deadline = time.monotonic()+2
        if process.returncode is None:
            owner = kernel.Anchor(process, bound)
            owner.kill_group(deadline=deadline)
            proof['host_pin_verified'] = True
            proof['decision_before_reap'] = owner.decided
            owner.reap(deadline)
        proof['host_reaped'] = process.returncode is not None
        while time.monotonic() < deadline:
            heartbeat()
            proof['host_absent'] = kernel.absent(process.pid)
            proof['group_absent'] = kernel.absent(bound.pgid, group=True)
            if proof['host_absent'] and proof['group_absent']:
                cleanup['status'] = 'RESOLVED'
                return cleanup
            select.select([], [], [], .005)
        raise kernel.IdentityUnknown('fixture group disappearance unconfirmed')
    except BaseException as error:
        error.fixture_cleanup = cleanup
        _FAILED_PROBES.append(SimpleNamespace(process=process, host_identity=bound,
                                            supervisor=None))
        raise  # Keep the actual cleanup location/type; the outer finally preserves primary.


def pinned():
    return {name: wire.file_hash(PACKAGE / name) for name in wire.ARTIFACTS}


def boundary_check():
    private = {'_git_containment.py', '_git_supervisor.py',
               '_git_containment_protocol.py', '_git_process_identity.py',
               '_git_containment_child.py', '_git_add_private.py', '_git_add_host.py'}
    markers = tuple(name[:-3] for name in private)
    checked = 0
    for path in PACKAGE.rglob('*.py'):
        if path.parent == PACKAGE and path.name in private:
            continue
        text = path.read_text()
        tree = ast.parse(text)
        # Covers import, importlib, __import__, -m, runpy, subprocess and
        # indirect wrappers anywhere in the production dependency closure.
        if any(marker in text for marker in markers):
            raise AssertionError('private containment referenced by public module')
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, 'module', '') or ''
                names = [alias.name for alias in node.names] + [module]
                if any(marker in name for marker in markers for name in names):
                    raise AssertionError('public containment import')
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                def literal(item):
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        return item.value
                    if isinstance(item, ast.BinOp) and isinstance(item.op, ast.Add):
                        left, right = literal(item.left), literal(item.right)
                        if left is not None and right is not None:
                            return left + right
                    return None
                value = literal(node)
                if value and any(marker in value for marker in markers):
                    raise AssertionError('indirect containment module string')
        checked += 1
    if not checked:
        raise AssertionError('empty production boundary scan')
    return checked


class ProtocolTests(unittest.TestCase):
    def test_fragmented_prefix_and_payload(self):
        r, w = os.pipe()
        try:
            reader = wire.Reader(r)
            raw = wire.frame(wire.message('CANCEL', nonce=NONCE))
            for byte in raw[:-1]:
                os.write(w, bytes([byte]))
                self.assertIsNone(reader.read())
            os.write(w, raw[-1:])
            self.assertEqual(reader.read()['type'], 'CANCEL')
        finally:
            os.close(r)
            os.close(w)

    def test_invalid_lengths_and_mid_frame_eof(self):
        for raw in (b'\0'*4, struct.pack('>I', wire.LIMIT+1), b'\0\0',
                    struct.pack('>I', 10)+b'{}'):
            with self.subTest(size=len(raw)):
                r, w = os.pipe()
                try:
                    os.write(w, raw)
                    os.close(w)
                    w = None
                    with self.assertRaises(wire.ProtocolFailure):
                        wire.Reader(r).receive(time.monotonic()+.1)
                finally:
                    os.close(r)
                    if w is not None:
                        os.close(w)

    def test_strict_json_and_unknown_schema(self):
        invalid = [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}',
                   b'['*9+b'0'+b']'*9, b'['+b'0,'*4096+b'0]', b'\xff']
        for raw in invalid:
            with self.assertRaises(wire.ProtocolFailure):
                wire.strict_load(raw)
        base = wire.message('CANCEL', nonce=NONCE)
        for change in ({'unknown': 1}, {'version': True}, {'version': 2},
                       {'type': 'OTHER'}, {'nonce': 'invalid'}):
            with self.assertRaises(wire.ProtocolFailure):
                wire.validate({**base, **change})

    def test_read_and_full_pipe_write_deadlines_and_epipe(self):
        r, w = os.pipe()
        try:
            reader = wire.Reader(r)
            with self.assertRaises(TimeoutError):
                reader.receive(time.monotonic()+.01)
            os.set_blocking(w, False)
            while True:
                try:
                    os.write(w, b'x'*4096)
                except BlockingIOError:
                    break
            with self.assertRaises(TimeoutError):
                wire.send(w, wire.message('CANCEL', nonce=NONCE), time.monotonic()+.01)
            os.close(r)
            r = None
            with self.assertRaises(BrokenPipeError):
                wire.send(w, wire.message('CANCEL', nonce=NONCE), time.monotonic()+.1)
        finally:
            if r is not None:
                os.close(r)
            os.close(w)

    def test_partial_writes_and_backpressure_reconstruct_one_frame(self):
        value = wire.message('CANCEL', nonce=NONCE)
        written = bytearray()
        attempts = [0]
        def partial(fd, data):
            attempts[0] += 1
            if attempts[0] == 1:
                raise BlockingIOError()
            count = min(3, len(data))
            written.extend(data[:count])
            return count
        with patch.object(wire.os, 'set_blocking'), \
                patch.object(wire.os, 'write', side_effect=partial), \
                patch.object(wire.select, 'select', return_value=([], [99], [])):
            wire.send(99, value, time.monotonic()+1)
        self.assertEqual(bytes(written), wire.frame(value))

    def test_expired_read_deadline_cannot_consume_a_buffered_frame(self):
        r, w = os.pipe()
        try:
            raw = wire.frame(wire.message('CANCEL', nonce=NONCE))
            os.write(w, raw)
            reader = wire.Reader(r)
            with self.assertRaises(TimeoutError):
                reader.receive(time.monotonic()-1)
            self.assertEqual(reader.receive(time.monotonic()+1)['type'], 'CANCEL')
        finally:
            os.close(r)
            os.close(w)

    def test_frame_finishing_after_deadline_is_rejected(self):
        reader = wire.Reader.__new__(wire.Reader)
        with patch.object(reader, 'read', return_value=wire.message('CANCEL', nonce=NONCE)), \
                patch.object(wire.time, 'monotonic', side_effect=[1, 3]):
            with self.assertRaises(TimeoutError):
                reader.receive(2)


class LibprocBoundaryTests(unittest.TestCase):
    """Exercise real _info/identity/Anchor code with the C call replaced.

    Synthetic kernel rows cannot establish native Darwin capability. Literal
    SDK sizes and argument assertions catch the original arg=0 BSD defect.
    """
    def setUp(self):
        self.bound = kernel.Identity(123, 123, 123, 501, 99, 1, 2)
        self.process = Mock(spec=subprocess.Popen, pid=123, returncode=None)

    def bsd(self, **changes):
        fields = self.bound.data()
        fields.pop('sid')
        return {**fields, 'status': 5, 'tpgid': 987, **changes}

    @contextmanager
    def libproc(self, rows, after_read=None):
        script = iter(rows)
        self.calls, self.events = [], []
        saved_errno = ctypes.get_errno()
        def call(pid, flavor, arg, buffer, size):
            # Assert actual C-boundary args, not the wrapper's mocked inputs.
            self.assertEqual(ctypes.get_errno(), 0)
            if flavor == 3:
                self.assertEqual((arg, size), (1, 136))
                model, row = kernel._BSD, next(script)
                self.events.append('bsd')
            else:
                self.assertEqual((flavor, arg, size), (4, 0, 96))
                model, row = kernel._Task, {'narrow9': 1}
                self.events.append('task')
            self.calls.append((pid, flavor, arg, size))
            value = ctypes.cast(buffer, ctypes.POINTER(model)).contents
            for key, item in row.items():
                if not key.startswith('_'):
                    setattr(value, key, item)
            if after_read is not None:
                after_read(len(self.calls))
            ctypes.set_errno(row.get('_errno', 0))
            return row.get('_returned', size)
        query = Mock(side_effect=call)
        with patch.object(kernel.sys, 'platform', 'darwin'), \
                patch.object(kernel.os, 'geteuid', return_value=501), \
                patch.object(kernel.os, 'getpid', return_value=99), \
                patch.object(kernel.ctypes, 'CDLL', return_value=SimpleNamespace(proc_pidinfo=query)) as library:
            try:
                ctypes.set_errno(errno.EIO)  # Must be cleared before every call.
                yield query
                if query.called:
                    self.assertEqual(query.argtypes, [ctypes.c_int, ctypes.c_int,
                        ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int])
                    self.assertIs(query.restype, ctypes.c_int)
                    for invocation in library.call_args_list:
                        self.assertEqual(invocation.args, ('/usr/lib/libproc.dylib',))
                        self.assertEqual(invocation.kwargs, {'use_errno': True})
            finally:
                ctypes.set_errno(saved_errno)

    def test_zombie_capable_bsd_and_unchanged_native_task_arguments(self):
        with self.libproc([self.bsd()]), \
                patch.object(kernel.threading, 'active_count', return_value=1), \
                patch.object(kernel.signal, 'getsignal', return_value=signal.SIG_DFL), \
                patch.object(kernel.select, 'kqueue', Mock(), create=True):
            row = kernel._info(self.bound.pid, 3, kernel._BSD)
            self.assertEqual((row.status, row.tpgid), (5, 987))
            kernel.host_check()  # Actual flavor-4 native-thread query path.
        self.assertEqual(self.calls, [(123, 3, 1, 136), (99, 4, 0, 96)])

    def test_unreviewed_flavor_or_model_rejects_before_c_call(self):
        for flavor, model in ((3, kernel._Task), (4, kernel._BSD), (5, kernel._BSD)):
            with self.subTest(flavor=flavor, model=model), self.libproc([]) as query:
                with self.assertRaises(kernel.NotSupported):
                    kernel._info(123, flavor, model)
                query.assert_not_called()

    def test_live_identity_reads_bsd_before_sid_then_fresh_bsd_before_signal(self):
        def sid(pid):
            self.assertEqual(pid, 123)
            self.events.append('sid')
            return 123
        def kill(*args):
            self.events.append('signal')
        with self.libproc([self.bsd(status=2), self.bsd(status=2)]), \
                patch.object(kernel, 'host_check'), \
                patch.object(kernel.os, 'getsid', side_effect=sid), \
                patch.object(kernel.os, 'killpg', side_effect=kill) as signal_group:
            anchor = kernel.Anchor(self.process, self.bound)
            anchor.kill_group()
        self.assertEqual(self.events, ['bsd', 'sid', 'bsd', 'signal'])
        self.assertEqual(anchor.verification['mode'], 'ordinary')
        signal_group.assert_called_once_with(123, signal.SIGKILL)
        self.process.wait.assert_not_called()

    def test_short_zero_and_errno_bsd_failures_never_reach_sid_or_signal(self):
        for returned in (0, 135, -1, 137):
            for number in (0, errno.ESRCH, errno.EPERM, errno.EACCES):
                with self.subTest(returned=returned, errno=number), \
                        self.libproc([{'_returned': returned, '_errno': number}]), \
                        patch.object(kernel, 'host_check'), \
                        patch.object(kernel.os, 'getsid') as sid, \
                        patch.object(kernel.os, 'killpg') as group, \
                        patch.object(kernel.os, 'kill') as leader:
                    anchor = kernel.Anchor(self.process, self.bound)
                    with self.assertRaises(kernel.IdentityUnknown) as caught:
                        anchor.kill_group()
                    evidence = wire.exception_evidence(caught.exception)
                    self.assertEqual(evidence['libproc'], {'flavor': 3, 'arg': 1,
                        'requested_bytes': 136, 'returned_bytes': returned, 'errno': number})
                    self.assertEqual(evidence['errno'], number)
                    self.assertEqual(evidence['message'], 'kernel identity unavailable')
                    self.assertEqual(len(self.calls), 1)
                    self.assertFalse(anchor.decided)
                    sid.assert_not_called()
                    group.assert_not_called()
                    leader.assert_not_called()
                    self.process.wait.assert_not_called()

    def test_task_failure_preserves_zero_argument_and_actual_errno(self):
        def denied(*args):
            self.assertEqual(ctypes.get_errno(), 0)
            ctypes.set_errno(errno.EPERM)
            return 0
        query = Mock(side_effect=denied)
        saved_errno = ctypes.get_errno()
        with patch.object(kernel.sys, 'platform', 'darwin'), \
                patch.object(kernel.os, 'geteuid', return_value=501), \
                patch.object(kernel.ctypes, 'CDLL', return_value=SimpleNamespace(proc_pidinfo=query)):
            try:
                ctypes.set_errno(errno.EIO)
                with self.assertRaises(kernel.IdentityUnknown) as caught:
                    kernel._info(99, 4, kernel._Task)
                self.assertEqual(query.call_args.args[:3], (99, 4, 0))
                self.assertEqual(query.call_args.args[4], 96)
                self.assertEqual(wire.exception_evidence(caught.exception)['libproc'],
                    {'flavor': 4, 'arg': 0, 'requested_bytes': 96,
                     'returned_bytes': 0, 'errno': errno.EPERM})
            finally:
                ctypes.set_errno(saved_errno)

    def test_owned_zombie_and_live_to_zombie_require_two_new_szomb_rows(self):
        for initial_status in (2, 5):
            def sid(pid):
                self.events.append('sid')
                raise ProcessLookupError(errno.ESRCH, 'SYNTHETIC_SECRET')
            def kill(*args):
                self.assertIsNone(self.process.returncode)
                self.process.wait.assert_not_called()
                self.events.append('signal')
            with self.subTest(initial_status=initial_status), \
                    self.libproc([self.bsd(status=initial_status), self.bsd(), self.bsd()]), \
                    patch.object(kernel, 'host_check'), \
                    patch.object(kernel.os, 'getsid', side_effect=sid), \
                    patch.object(kernel.os, 'killpg', side_effect=kill):
                anchor = kernel.Anchor(self.process, self.bound)
                anchor.kill_group()
                self.assertEqual(self.events, ['bsd', 'sid', 'bsd', 'bsd', 'signal'])
                self.assertEqual(anchor.verification['mode'], 'owned-zombie')
                self.assertEqual(anchor.verification['sid_source'], 'launch-continuity')
                self.assertEqual([r['status'] for r in anchor.verification['kernel_rows']], [5, 5])
                self.assertEqual(len(self.calls), 3)

    def test_general_identity_esrch_and_sid_permission_never_grant_zombie_authority(self):
        for error in (ProcessLookupError(errno.ESRCH, 'SYNTHETIC_SECRET'),
                      PermissionError(errno.EPERM, 'SYNTHETIC_SECRET')):
            with self.subTest(error=type(error)), self.libproc([self.bsd()]), \
                    patch.object(kernel.os, 'getsid', side_effect=error):
                with self.assertRaises(type(error)):
                    kernel.identity(123)
                self.assertEqual(len(self.calls), 1)
        with self.libproc([self.bsd()]), patch.object(kernel, 'host_check'), \
                patch.object(kernel.os, 'getsid', side_effect=PermissionError(errno.EPERM, 'private')), \
                patch.object(kernel.os, 'killpg') as kill:
            with self.assertRaises(PermissionError):
                kernel.Anchor(self.process, self.bound).kill_group()
            self.assertEqual(len(self.calls), 1)
            kill.assert_not_called()

    def test_changed_live_start_identity_or_sid_never_signals(self):
        for key in ('pid', 'pgid', 'ppid', 'uid', 'seconds', 'micros', 'sid'):
            rows = [self.bsd(status=2), self.bsd(status=2)]
            if key != 'sid':
                rows[1][key] += 1
            with self.subTest(key=key), self.libproc(rows), \
                    patch.object(kernel, 'host_check'), \
                    patch.object(kernel.os, 'getsid', return_value=124 if key == 'sid' else 123), \
                    patch.object(kernel.os, 'killpg') as kill:
                with self.assertRaises(kernel.IdentityUnknown):
                    kernel.Anchor(self.process, self.bound).kill_group()
                kill.assert_not_called()

    def test_either_zombie_proof_row_mismatch_reuse_or_missing_never_signals(self):
        changed = [self.bsd(**{key: getattr(self.bound, key)+1})
                   for key in ('pid', 'pgid', 'ppid', 'uid', 'seconds', 'micros')]
        changed.extend((self.bsd(status=2), self.bsd(status=0),
            {'_returned': 0, '_errno': errno.ESRCH},
            {'_returned': 0, '_errno': errno.EPERM}, {'_returned': 135}))
        for bad in changed:
            for index in (0, 1):
                rows = [self.bsd(), self.bsd()]
                rows[index] = bad
                with self.subTest(bad=bad, index=index), \
                        self.libproc([self.bsd(), *rows]), \
                        patch.object(kernel, 'host_check'), \
                        patch.object(kernel.os, 'getsid', side_effect=ProcessLookupError(errno.ESRCH, 'private')), \
                        patch.object(kernel.os, 'killpg') as group, \
                        patch.object(kernel.os, 'kill') as leader:
                    anchor = kernel.Anchor(self.process, self.bound)
                    with self.assertRaises(kernel.IdentityUnknown):
                        anchor.kill_group()
                    group.assert_not_called()
                    leader.assert_not_called()
                    self.assertFalse(anchor.decided)
                    self.process.wait.assert_not_called()

    def test_reap_during_second_fresh_zombie_row_never_signals(self):
        def reap(count):
            if count == 3:  # Initial BSD then two fresh owned-zombie reads.
                self.process.returncode = 0
        with self.libproc([self.bsd(), self.bsd(), self.bsd()], after_read=reap), \
                patch.object(kernel, 'host_check'), \
                patch.object(kernel.os, 'getsid', side_effect=ProcessLookupError(errno.ESRCH, 'private')), \
                patch.object(kernel.os, 'killpg') as group, patch.object(kernel.os, 'kill') as leader:
            with self.assertRaises(kernel.IdentityUnknown):
                kernel.Anchor(self.process, self.bound).kill_group()
            self.assertEqual(len(self.calls), 3)
            group.assert_not_called()
            leader.assert_not_called()
            self.process.wait.assert_not_called()

    def test_verification_to_signal_race_uses_repaired_bsd_boundary_without_reap(self):
        reads = [self.bsd(status=2), self.bsd(status=2), self.bsd(),
                 self.bsd(), self.bsd(), self.bsd()]
        note = Mock()
        note.exited.return_value = True
        def sid(pid):
            self.events.append('sid')
            if len(self.calls) > 2:
                raise ProcessLookupError(errno.ESRCH, 'private')
            return 123
        def signal_leader(*args):
            self.events.append('leader')
        def signal_group(*args):
            self.events.append('group')
            self.process.wait.assert_not_called()
            self.assertIsNone(self.process.returncode)
        with self.libproc(reads), patch.object(kernel, 'host_check'), \
                patch.object(kernel, 'ExitNote', return_value=note), \
                patch.object(kernel.os, 'getsid', side_effect=sid), \
                patch.object(kernel.os, 'kill', side_effect=signal_leader), \
                patch.object(kernel.os, 'killpg', side_effect=signal_group):
            anchor = kernel.Anchor(self.process, self.bound)
            anchor.kill_group(exit_after_verify=True, deadline=time.monotonic()+1)
        self.assertEqual(self.events, ['bsd', 'sid', 'bsd', 'leader', 'bsd',
                                      'bsd', 'sid', 'bsd', 'bsd', 'group'])
        self.assertEqual(anchor.verification['mode'], 'owned-zombie')
        self.assertEqual(len(self.calls), 6)
        note.close.assert_called_once()
        self.process.wait.assert_not_called()

    def test_exit_transition_polls_matching_status_two_until_two_fresh_szomb_rows(self):
        rows = [self.bsd(status=2), self.bsd(status=2), self.bsd(status=2),
                self.bsd(), self.bsd(status=2), self.bsd(), self.bsd()]
        with self.libproc(rows), patch.object(kernel, 'host_check'), \
                patch.object(kernel.os, 'getsid', side_effect=ProcessLookupError(errno.ESRCH, 'private')), \
                patch.object(kernel.select, 'select') as pause, \
                patch.object(kernel.time, 'monotonic', return_value=1), \
                patch.object(kernel.os, 'killpg') as group, patch.object(kernel.os, 'kill') as leader:
            anchor = kernel.Anchor(self.process, self.bound)
            anchor.kill_group(deadline=2)
        group.assert_called_once_with(123, signal.SIGKILL)
        leader.assert_not_called()
        self.assertEqual(pause.call_count, 3)
        self.assertEqual(anchor.verification['observations'], 6)
        self.assertEqual([item['status'] for item in anchor.verification['kernel_rows']], [5, 5])
        self.assertEqual([item['status'] for item in anchor.verification['transition_rows']], [2, 2])
        for method in (self.process.poll, self.process.wait, self.process.communicate):
            method.assert_not_called()

    def test_transition_expiry_keeps_observed_status_two_and_never_signals_or_reaps(self):
        clock = [1.0]
        def pause(*args):
            self.assertLessEqual(args[-1], .005)
            clock[0] = 2.0
        with self.libproc([self.bsd(status=2), self.bsd(status=2)]), \
                patch.object(kernel, 'host_check'), \
                patch.object(kernel.os, 'getsid', side_effect=ProcessLookupError(errno.ESRCH, 'private')), \
                patch.object(kernel.select, 'select', side_effect=pause), \
                patch.object(kernel.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(kernel.os, 'killpg') as group, patch.object(kernel.os, 'kill') as leader:
            anchor = kernel.Anchor(self.process, self.bound)
            with self.assertRaises(kernel.IdentityUnknown):
                anchor.kill_group(deadline=2)
        self.assertEqual(anchor.verification['mode'], 'unconfirmed-zombie')
        self.assertEqual(anchor.verification['kernel_status'], 2)
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(anchor.decided)
        group.assert_not_called()
        leader.assert_not_called()
        self.process.wait.assert_not_called()

    def test_drift_missing_short_permission_or_external_reap_during_transition_never_signals(self):
        bad_rows = [self.bsd(**{key: getattr(self.bound, key)+1})
                    for key in ('pid', 'pgid', 'uid', 'ppid', 'seconds', 'micros')]
        bad_rows += [{'_returned': 0, '_errno': errno.ESRCH}, {'_returned': 135},
                     {'_returned': 0, '_errno': errno.EPERM}, self.bsd(status=0), self.bsd()]
        for bad in bad_rows:
            self.process.returncode = None
            def after(count):
                if bad == self.bsd() and count == 3:
                    self.process.returncode = 0  # Simulate forbidden foreign reap.
            with self.subTest(bad=bad), \
                    self.libproc([self.bsd(status=2), self.bsd(status=2), bad], after_read=after), \
                    patch.object(kernel, 'host_check'), \
                    patch.object(kernel.os, 'getsid', side_effect=ProcessLookupError(errno.ESRCH, 'private')), \
                    patch.object(kernel.select, 'select'), \
                    patch.object(kernel.time, 'monotonic', return_value=1), \
                    patch.object(kernel.os, 'killpg') as group, patch.object(kernel.os, 'kill') as leader:
                anchor = kernel.Anchor(self.process, self.bound)
                with self.assertRaises(kernel.IdentityUnknown):
                    anchor.kill_group(deadline=2)
                group.assert_not_called()
                leader.assert_not_called()
                self.process.wait.assert_not_called()
                self.assertFalse(anchor.decided)
        self.process.returncode = None

    def test_deadline_expiring_during_second_szomb_read_cannot_grant_signal(self):
        clock = [1]
        def expire(count):
            if count == 3:
                clock[0] = 2
        with self.libproc([self.bsd(), self.bsd(), self.bsd()], after_read=expire), \
                patch.object(kernel, 'host_check'), \
                patch.object(kernel.os, 'getsid', side_effect=ProcessLookupError(errno.ESRCH, 'private')), \
                patch.object(kernel.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(kernel.os, 'killpg') as group:
            with self.assertRaises(kernel.IdentityUnknown):
                kernel.Anchor(self.process, self.bound).kill_group(deadline=2)
        group.assert_not_called()
        self.process.wait.assert_not_called()

    def test_real_query_failure_remains_secondary_to_primary_fixture_failure(self):
        primary = wire.ProtocolFailure('invalid containment protocol')
        with self.libproc([{'_returned': 0, '_errno': errno.EPERM}]), \
                patch.object(kernel, 'host_check'), \
                patch.object(kernel.os, 'getsid') as sid, \
                patch.object(kernel.os, 'killpg') as kill:
            with self.assertRaises(wire.ProtocolFailure) as caught:
                try:
                    raise primary
                finally:
                    cleanup_preserving_failure(lambda: cleanup_fixture(self.process, self.bound))
            try:
                self.assertIs(caught.exception, primary)
                evidence = primary.secondary_cleanup_errors[0]
                self.assertEqual(evidence['exception_type'], 'IdentityUnknown')
                self.assertEqual(evidence['libproc']['returned_bytes'], 0)
                self.assertEqual(evidence['libproc']['errno'], errno.EPERM)
                self.assertEqual(evidence['libproc']['arg'], 1)
                self.process.wait.assert_not_called()
                sid.assert_not_called()
                kill.assert_not_called()
            finally:
                _FAILED_PROBES[:] = [p for p in _FAILED_PROBES if p.process is not self.process]

    def test_formatter_only_accepts_bounded_numeric_api_evidence(self):
        valid = {'flavor': 3, 'arg': 1, 'requested_bytes': 136,
                 'returned_bytes': 0, 'errno': errno.EPERM}
        error = kernel.IdentityUnknown('kernel identity unavailable')
        error.libproc = valid
        self.assertEqual(wire.exception_evidence(error)['libproc'], valid)
        malformed = [None, 'SYNTHETIC_SECRET', {**valid, 'argv': 'SYNTHETIC_SECRET'},
            {**valid, 'returned_bytes': 'SYNTHETIC_SECRET'}, {**valid, 'arg': True},
            {**valid, 'flavor': 4}, {**valid, 'arg': 0},
            {**valid, 'requested_bytes': 4097}, {**valid, 'returned_bytes': 2**31},
            {**valid, 'errno': -1}]
        for metadata in malformed:
            with self.subTest(metadata=metadata):
                error.libproc = metadata
                data = wire.exception_evidence(error)
                self.assertNotIn('libproc', data)
                self.assertNotIn(b'SYNTHETIC_SECRET', wire.canonical(data))


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.bound = kernel.Identity(123, 123, 123, os.geteuid(), os.getpid(), 1, 2)
        self.process = Mock(spec=subprocess.Popen, pid=123, returncode=None)
        # Deterministic ownership tests never supply native capability evidence.
        if self._testMethodName != 'test_host_rejects_python_threads_native_threads_and_custom_reapers':
            check = patch.object(kernel, 'host_check')
            self.host_check = check.start()
            self.addCleanup(check.stop)

    def bsd(self, **changes):
        fields = self.bound.data()
        fields.pop('sid')
        return SimpleNamespace(**{**fields, 'status': 5, 'tpgid': 987, **changes})

    def test_anchor_decision_precedes_reap_and_no_signal_after_reap(self):
        anchor = kernel.Anchor(self.process, self.bound)
        calls = []
        with patch.object(kernel, 'identity', return_value=self.bound), \
                patch.object(kernel.os, 'killpg', side_effect=lambda *args: calls.append('kill')):
            self.process.wait.side_effect = lambda **kwargs: calls.append('reap')
            with self.assertRaises(kernel.IdentityUnknown):
                anchor.reap(time.monotonic()+1)
            anchor.kill_group()
            anchor.kill_group()
            anchor.reap(time.monotonic()+1)
            with self.assertRaises(kernel.IdentityUnknown):
                anchor.kill_group()
        self.assertEqual(calls, ['kill', 'reap'])

    def test_mismatch_reuse_and_released_ownership_never_signal(self):
        for observed in (replace(self.bound, micros=3), replace(self.bound, pgid=124),
                         replace(self.bound, sid=124), replace(self.bound, uid=999)):
            with patch.object(kernel, 'identity', return_value=observed), \
                    patch.object(kernel.os, 'killpg') as kill:
                with self.assertRaises(kernel.IdentityUnknown):
                    kernel.Anchor(self.process, self.bound).kill_group()
                kill.assert_not_called()
        self.process.returncode = 0
        with patch.object(kernel.os, 'killpg') as kill:
            with self.assertRaises(kernel.IdentityUnknown):
                kernel.Anchor(self.process, self.bound).kill_group()
            kill.assert_not_called()

    def test_verification_to_signal_exit_keeps_owned_pin(self):
        anchor = kernel.Anchor(self.process, self.bound)
        self.process.wait.side_effect = AssertionError('premature reap')
        def signal_group(*args):
            self.assertIsNone(self.process.returncode)
            self.assertFalse(anchor.reaped)
            self.process.wait.assert_not_called()
        with patch.object(kernel, 'identity', return_value=self.bound), \
                patch.object(kernel.os, 'killpg', side_effect=signal_group):
            anchor.kill_group()
        self.assertTrue(anchor.decided)

    def test_permission_is_not_disappearance(self):
        with patch.object(kernel.os, 'killpg', side_effect=PermissionError(errno.EPERM, 'fixed')):
            with self.assertRaises(PermissionError):
                kernel.absent(123, group=True)
        with patch.object(kernel.os, 'kill', side_effect=ProcessLookupError):
            self.assertTrue(kernel.absent(123))

    def test_group_failure_uses_only_pinned_leader_and_retains_secondary_faults(self):
        anchor = kernel.Anchor(self.process, self.bound)
        calls = []
        def denied_group(*args):
            calls.append('group')
            raise PermissionError(errno.EPERM, 'synthetic private text')
        def denied_leader(pid, number):
            self.assertEqual((pid, number), (self.bound.pid, signal.SIGKILL))
            self.assertIsNone(self.process.returncode)
            self.process.wait.assert_not_called()
            calls.append('leader')
            raise PermissionError(errno.EPERM, 'synthetic private text')
        with patch.object(kernel, 'identity', return_value=self.bound), \
                patch.object(kernel.os, 'killpg', side_effect=denied_group), \
                patch.object(kernel.os, 'kill', side_effect=denied_leader):
            anchor.kill_group()
            self.assertTrue(anchor.decided)
            self.assertFalse(anchor.reaped)
            self.assertEqual(anchor.signal_faults, ['PermissionError', 'PermissionError'])
            anchor.kill_group()
        self.assertEqual(calls, ['group', 'leader'])
        self.process.wait.assert_not_called()

    def test_fixed_race_injection_preserves_supervisor_pin_before_group_signal(self):
        calls = []
        def verify(pid):
            calls.append('verify')
            return self.bound
        note = Mock()
        note.exited.return_value = True
        anchor = kernel.Anchor(self.process, self.bound)
        self.process.wait.side_effect = lambda **kwargs: calls.append('reap')
        with patch.object(kernel, 'identity', side_effect=verify), \
                patch.object(kernel, 'ExitNote', return_value=note), \
                patch.object(kernel, '_info', return_value=self.bsd()), \
                patch.object(kernel.os, 'kill', side_effect=lambda *args: calls.append('leader')), \
                patch.object(kernel.os, 'killpg', side_effect=lambda *args: calls.append('group')):
            anchor.kill_group(exit_after_verify=True, deadline=time.monotonic()+1)
            self.process.wait.assert_not_called()
            anchor.reap(time.monotonic()+1)
        self.assertEqual(calls, ['verify', 'leader', 'verify', 'group', 'reap'])
        note.close.assert_called_once()

    def test_general_identity_never_accepts_esrch_or_changed_sid(self):
        with patch.object(kernel, '_info', return_value=self.bsd()), \
                patch.object(kernel.os, 'getsid', side_effect=ProcessLookupError(errno.ESRCH, 'private')):
            with self.assertRaises(ProcessLookupError):
                kernel.identity(self.bound.pid)
        with patch.object(kernel, '_info', side_effect=[self.bsd(status=2), self.bsd(pgid=124)]), \
                patch.object(kernel.os, 'getsid', return_value=self.bound.sid):
            with self.assertRaises(kernel.IdentityUnknown):
                kernel.identity(self.bound.pid)
        with patch.object(kernel, 'identity', return_value=replace(self.bound, sid=124)), \
                patch.object(kernel, '_info') as query, \
                patch.object(kernel.os, 'killpg') as kill:
            with self.assertRaises(kernel.IdentityUnknown):
                kernel.Anchor(self.process, self.bound).kill_group()
            query.assert_not_called()
            kill.assert_not_called()

    def test_owned_zombie_esrch_requires_two_matching_native_rows_before_signal(self):
        anchor = kernel.Anchor(self.process, self.bound)
        calls = []
        def row(*args):
            calls.append('kernel')
            return self.bsd()  # tpgid differs from SID and must be ignored.
        with patch.object(kernel, 'identity', side_effect=ProcessLookupError(errno.ESRCH, 'private')), \
                patch.object(kernel, '_info', side_effect=row), \
                patch.object(kernel.os, 'killpg', side_effect=lambda *args: calls.append('signal')):
            anchor.kill_group()
        self.assertEqual(calls, ['kernel', 'kernel', 'signal'])
        self.assertEqual(anchor.verification['mode'], 'owned-zombie')
        self.assertEqual(anchor.verification['sid_source'], 'launch-continuity')
        self.assertEqual(anchor.verification['sid_query'], 'ESRCH')
        self.assertEqual(anchor.verification['kernel_status'], 5)
        self.assertEqual(len(anchor.verification['kernel_rows']), 2)
        for row in anchor.verification['kernel_rows']:
            self.assertEqual(row['status'], 5)
            self.assertEqual(row['pid'], self.bound.pid)
        self.assertTrue(anchor.decided)
        self.process.wait.assert_not_called()

    def test_live_to_zombie_sid_query_race_uses_only_fresh_zombie_rows(self):
        anchor = kernel.Anchor(self.process, self.bound)
        with patch.object(kernel, '_info', side_effect=[self.bsd(status=2), self.bsd(), self.bsd()]), \
                patch.object(kernel.os, 'getsid', side_effect=ProcessLookupError(errno.ESRCH, 'private')), \
                patch.object(kernel.os, 'killpg') as kill:
            anchor.kill_group()
        kill.assert_called_once_with(self.bound.pgid, signal.SIGKILL)
        self.assertEqual(anchor.verification['mode'], 'owned-zombie')

    def test_zombie_mismatch_reuse_state_and_missing_rows_never_signal(self):
        changed = [self.bsd(**{key: getattr(self.bound, key)+1})
                   for key in ('pid', 'pgid', 'uid', 'ppid', 'seconds', 'micros')]
        changed.extend((self.bsd(status=2), self.bsd(status=0)))
        for row in changed:
            for rows in ([row, self.bsd()], [self.bsd(), row]):
                with self.subTest(rows=rows), \
                        patch.object(kernel, 'identity', side_effect=ProcessLookupError(errno.ESRCH, 'private')), \
                        patch.object(kernel, '_info', side_effect=rows), \
                        patch.object(kernel.os, 'killpg') as kill:
                    with self.assertRaises(kernel.IdentityUnknown):
                        kernel.Anchor(self.process, self.bound).kill_group()
                    kill.assert_not_called()
        for error in (kernel.IdentityUnknown('kernel identity unavailable'), PermissionError(errno.EPERM, 'private')):
            with patch.object(kernel, 'identity', side_effect=ProcessLookupError(errno.ESRCH, 'private')), \
                    patch.object(kernel, '_info', side_effect=error), \
                    patch.object(kernel.os, 'killpg') as kill:
                with self.assertRaises(type(error)):
                    kernel.Anchor(self.process, self.bound).kill_group()
                kill.assert_not_called()

    def test_non_esrch_sid_fault_and_unprovable_host_never_enter_zombie_proof(self):
        for error in (PermissionError(errno.EPERM, 'private'), ProcessLookupError()):
            with patch.object(kernel, 'identity', side_effect=error), \
                    patch.object(kernel, '_info') as query, patch.object(kernel.os, 'killpg') as kill:
                with self.assertRaises(type(error)):
                    kernel.Anchor(self.process, self.bound).kill_group()
                query.assert_not_called()
                kill.assert_not_called()
        self.host_check.side_effect = kernel.NotSupported('single-thread default-reaper host required')
        with patch.object(kernel, 'identity') as query, patch.object(kernel.os, 'killpg') as kill:
            with self.assertRaises(kernel.NotSupported):
                kernel.Anchor(self.process, self.bound).kill_group()
            query.assert_not_called()
            kill.assert_not_called()

    def test_reap_race_or_non_popen_cannot_supply_zombie_pin(self):
        count = [0]
        def raced_row(*args):
            count[0] += 1
            if count[0] == 2:
                self.process.returncode = 0  # A forbidden foreign reaper releases the pin.
            return self.bsd()
        with patch.object(kernel, 'identity', side_effect=ProcessLookupError(errno.ESRCH, 'private')), \
                patch.object(kernel, '_info', side_effect=raced_row), patch.object(kernel.os, 'killpg') as kill:
            with self.assertRaises(kernel.IdentityUnknown):
                kernel.Anchor(self.process, self.bound).kill_group()
            kill.assert_not_called()
        with patch.object(kernel, 'identity') as query, patch.object(kernel.os, 'killpg') as kill:
            with self.assertRaises(kernel.IdentityUnknown):
                kernel.Anchor(SimpleNamespace(pid=123, returncode=None), self.bound).kill_group()
            query.assert_not_called()
            kill.assert_not_called()

    def test_race_identity_failure_survives_notification_close_failure(self):
        anchor = kernel.Anchor(self.process, self.bound)
        primary = kernel.IdentityUnknown('owned zombie continuity unconfirmed')
        note = Mock()
        note.exited.return_value = True
        note.close.side_effect = PermissionError(errno.EPERM, 'SYNTHETIC_SECRET')
        with patch.object(kernel, 'identity', side_effect=[self.bound, primary]), \
                patch.object(kernel, 'ExitNote', return_value=note), \
                patch.object(kernel, '_info', return_value=self.bsd()), \
                patch.object(kernel.os, 'kill'), patch.object(kernel.os, 'killpg') as kill:
            with self.assertRaises(kernel.IdentityUnknown) as caught:
                anchor.kill_group(exit_after_verify=True, deadline=time.monotonic()+1)
        self.assertIs(caught.exception, primary)
        kill.assert_not_called()
        self.process.wait.assert_not_called()
        self.assertFalse(anchor.decided)
        self.assertEqual(anchor.monitor_faults[0]['exception_type'], 'PermissionError')
        self.assertNotIn(b'SYNTHETIC_SECRET', wire.canonical(anchor.monitor_faults))

    def test_host_rejects_python_threads_native_threads_and_custom_reapers(self):
        with patch.object(kernel.threading, 'active_count', return_value=2):
            with self.assertRaises(kernel.NotSupported):
                kernel.host_check()
        with patch.object(kernel.signal, 'getsignal', return_value=signal.SIG_IGN):
            with self.assertRaises(kernel.NotSupported):
                kernel.host_check()
        with patch.object(kernel, '_info', return_value=Mock(narrow9=2)), \
                patch.object(kernel.signal, 'getsignal', return_value=signal.SIG_DFL):
            with self.assertRaises(kernel.NotSupported):
                kernel.host_check()

    def test_registration_failure_closes_notification_fd(self):
        queue = Mock()
        queue.control.side_effect = OSError('synthetic registration failure')
        with patch.object(kernel.select, 'kqueue', return_value=queue, create=True), \
                patch.object(kernel.select, 'kevent', return_value=Mock(), create=True), \
                patch.object(kernel.select, 'KQ_FILTER_PROC', 1, create=True), \
                patch.object(kernel.select, 'KQ_EV_ADD', 1, create=True), \
                patch.object(kernel.select, 'KQ_EV_ENABLE', 2, create=True), \
                patch.object(kernel.select, 'KQ_NOTE_EXIT', 1, create=True):
            with self.assertRaises(OSError):
                kernel.ExitNote(123)
        queue.close.assert_called_once()

    def test_note_exit_latches_across_empty_reads_but_rejects_later_invalid_events(self):
        queue = Mock()
        event = SimpleNamespace(ident=123, filter=1, flags=0, fflags=4)
        bad = SimpleNamespace(ident=124, filter=1, flags=0, fflags=4)
        queue.control.side_effect = [[], [event], [], [bad]]
        with patch.object(kernel.select, 'kqueue', return_value=queue, create=True), \
                patch.object(kernel.select, 'kevent', return_value=Mock(), create=True), \
                patch.object(kernel.select, 'KQ_FILTER_PROC', 1, create=True), \
                patch.object(kernel.select, 'KQ_EV_ADD', 1, create=True), \
                patch.object(kernel.select, 'KQ_EV_ENABLE', 2, create=True), \
                patch.object(kernel.select, 'KQ_EV_ERROR', 8, create=True), \
                patch.object(kernel.select, 'KQ_NOTE_EXIT', 4, create=True):
            note = kernel.ExitNote(123)
            self.assertTrue(note.exited())
            self.assertTrue(note.exited())
            with self.assertRaises(kernel.IdentityUnknown):
                note.exited()
            note.close()
        queue.close.assert_called_once()

    def test_parent_reparenting_keeps_supervisors_self_anchor(self):
        module = importlib.import_module('llmtool._git_supervisor')
        class LastAct(BaseException):
            pass
        rows = [SimpleNamespace(**{**self.bound.data(), 'ppid': ppid})
                for ppid in (456, 1)]
        # Prove the real double-read query rejects only the changing PPID.
        with patch.object(kernel, '_info', side_effect=rows), \
                patch.object(kernel.os, 'getsid', return_value=self.bound.sid):
            with self.assertRaises(kernel.IdentityUnknown):
                kernel.identity(self.bound.pid)
        for failure in (kernel.IdentityUnknown('PPID race'), OSError(errno.EIO, 'libproc'),
                        kernel.NotSupported('inspection unavailable')):
            with self.subTest(failure=type(failure).__name__), \
                    patch.object(module.os, 'getpid', return_value=self.bound.pid), \
                    patch.object(module.os, 'getpgrp', return_value=self.bound.pid), \
                    patch.object(module, 'identity', side_effect=failure) as lookup, \
                    patch.object(module.os, 'killpg', side_effect=LastAct) as kill, \
                    patch.object(module.os, '_exit') as exit_process:
                with self.assertRaises(LastAct):
                    module._last_act(self.bound)
                kill.assert_called_once_with(0, signal.SIGKILL)
                lookup.assert_not_called()
                exit_process.assert_not_called()

    def test_last_act_rejects_other_groups_and_stale_anchor(self):
        module = importlib.import_module('llmtool._git_supervisor')
        class LastAct(BaseException):
            pass
        for pid, pgid, own in ((123, 456, self.bound),
                              (124, 124, self.bound),
                              (123, 123, replace(self.bound, pgid=456)),
                              (123, 123, replace(self.bound, sid=456))):
            with self.subTest(pid=pid, pgid=pgid, own=own), \
                    patch.object(module.os, 'getpid', return_value=pid), \
                    patch.object(module.os, 'getpgrp', return_value=pgid), \
                    patch.object(module.os, 'killpg') as kill, \
                    patch.object(module.os, '_exit', side_effect=LastAct):
                with self.assertRaises(LastAct):
                    module._last_act(own)
                kill.assert_not_called()


class BindingBoundaryTests(unittest.TestCase):
    @contextmanager
    def trusted_interpreter(self):
        # Binding unit tests pin controlled bytes; no interpreter is executed.
        # Ambient CI interpreters need not satisfy the deployment trust premise.
        with tempfile.TemporaryDirectory() as directory:
            interpreter = Path(directory).resolve() / 'python-fixture'
            interpreter.write_bytes(b'synthetic pinned interpreter\n')
            interpreter.chmod(0o700)
            yield interpreter

    def test_complete_pinned_manifest_and_drift_rejection(self):
        with self.trusted_interpreter() as interpreter, \
                patch.object(wire.sys, 'executable', str(interpreter)):
            manifest = pinned()
            binding = wire.runtime_binding(manifest)
            self.assertEqual(binding['executable'], str(interpreter))
            self.assertEqual(binding['interpreter'], str(interpreter))
            self.assertEqual(binding['interpreter_sha256'],
                             hashlib.sha256(interpreter.read_bytes()).hexdigest())
            self.assertEqual(len(wire.verify_binding(binding)), 64)
            changed = {**manifest, '_git_supervisor.py': '0'*64}
            with self.assertRaises(wire.ProtocolFailure):
                wire.runtime_binding(changed)
            with patch.object(wire.importlib.util, 'find_spec', return_value=None):
                with self.assertRaises(wire.ProtocolFailure):
                    wire.runtime_binding(manifest)
            self.assertIn('git_mutation.py', manifest)
            self.assertIn('errors.py', manifest)
            for change in ({'protocol': True}, {'action_digest': '0'*64},
                           {'fixture': {'case': 'wait', 'timeout': 2}}):
                with self.assertRaises(wire.ProtocolFailure):
                    wire.verify_binding({**binding, **change})
            self.assertNotEqual(binding['action_digest'],
                                wire.runtime_binding(manifest, 'wait')['action_digest'])

    def test_writable_interpreter_rejected_by_runtime_binding(self):
        manifest = pinned()
        with self.trusted_interpreter() as interpreter, \
                patch.object(wire.sys, 'executable', str(interpreter)):
            for mode in (0o777, 0o720, 0o702):
                with self.subTest(mode=oct(mode)):
                    interpreter.chmod(mode)
                    with self.assertRaises(wire.ProtocolFailure):
                        wire.runtime_binding(manifest)

    def test_file_hash_requires_regular_owned_bounded_trusted_file(self):
        with self.trusted_interpreter() as interpreter:
            self.assertEqual(wire.file_hash(interpreter),
                             hashlib.sha256(interpreter.read_bytes()).hexdigest())
            before = interpreter.stat()
            untrusted_owner = SimpleNamespace(st_mode=before.st_mode,
                st_uid=max(0, os.geteuid())+1, st_size=before.st_size)
            with patch.object(wire.os, 'fstat', return_value=untrusted_owner):
                with self.assertRaises(wire.ProtocolFailure):
                    wire.file_hash(interpreter)
            directory = interpreter.parent / 'directory'
            directory.mkdir(mode=0o700)
            # fdopen rejects a directory before the metadata predicate.
            directory_fds = []
            real_open = os.open
            def open_directory(*args, **kwargs):
                fd = real_open(*args, **kwargs)
                directory_fds.append(fd)
                self.assertTrue(stat.S_ISDIR(os.fstat(fd).st_mode))
                return fd
            with patch.object(wire.os, 'open', side_effect=open_directory):
                try:
                    with self.assertRaises(IsADirectoryError):
                        wire.file_hash(directory)
                    self.assertEqual(len(directory_fds), 1)
                finally:
                    for fd in directory_fds:
                        try:
                            os.close(fd)
                        except OSError as error:
                            if error.errno != errno.EBADF:
                                raise
                            self.assertEqual(error.errno, errno.EBADF)
            fifo = interpreter.parent / 'fifo'
            os.mkfifo(fifo, mode=0o600)
            with self.assertRaises(wire.ProtocolFailure):
                wire.file_hash(fifo)
            alias = interpreter.parent / 'alias'
            alias.symlink_to(interpreter)
            with patch.object(wire.os, 'open', wraps=os.open) as opened:
                with self.assertRaises(wire.ProtocolFailure):
                    wire.file_hash(alias)
                opened.assert_not_called()
            with interpreter.open('wb') as stream:
                stream.truncate(64*1024*1024+1)
            with self.assertRaises(wire.ProtocolFailure):
                wire.file_hash(interpreter)

    def test_file_hash_rejects_read_and_named_identity_drift(self):
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        with self.trusted_interpreter() as interpreter:
            before = interpreter.stat()
            real_stat = os.stat
            for stage in ('read', 'named'):
                for field in fields:
                    with self.subTest(stage=stage, field=field):
                        values = {name: getattr(before, name) for name in fields}
                        values.update(st_mode=before.st_mode, st_uid=before.st_uid)
                        values[field] += 1
                        changed = SimpleNamespace(**values)
                        if stage == 'read':
                            with patch.object(wire.os, 'fstat', side_effect=[before, changed]):
                                with self.assertRaises(wire.ProtocolFailure):
                                    wire.file_hash(interpreter)
                        else:
                            def named_stat(path, *args, **kwargs):
                                observed = real_stat(path, *args, **kwargs)
                                if Path(path) == interpreter and kwargs.get('follow_symlinks') is False:
                                    return changed
                                return observed
                            with patch.object(wire.os, 'stat', side_effect=named_stat):
                                with self.assertRaises(wire.ProtocolFailure):
                                    wire.file_hash(interpreter)

    def test_environment_is_fresh(self):
        with patch.dict(os.environ, {'PYTHONPATH': 'hostile', 'DYLD_INSERT_LIBRARIES': 'hostile',
                        'TOKEN': 'synthetic', 'HTTP_PROXY': 'synthetic'}):
            self.assertEqual(wire.fresh_environment(), {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})

    def test_static_boundary_and_no_product_callers(self):
        self.assertGreater(boundary_check(), 0)
        text = (PACKAGE / '_git_containment.py').read_text()
        self.assertNotIn('preflight_git_add(', text)
        self.assertNotIn('git add', text)
        self.assertNotIn('shell=True', text)
        for name in ('_git_containment.py', '_git_supervisor.py', '_git_containment_child.py'):
            tree = ast.parse((PACKAGE/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    self.assertNotIn(getattr(node.func, 'attr', ''),
                                     ('fork', 'forkserver', 'Thread', 'communicate', 'poll'))
                    if getattr(node.func, 'attr', '') == 'Popen':
                        keywords = {k.arg: k.value for k in node.keywords}
                        self.assertIs(keywords['shell'].value, False)
                        self.assertIs(keywords['close_fds'].value, True)

    def test_direct_import_and_runpy_cannot_spawn(self):
        with patch.object(subprocess, 'Popen') as spawn:
            module = importlib.import_module('llmtool._git_supervisor')
            self.assertEqual(module._main(), 78)
            with self.assertRaises(SystemExit) as caught:
                runpy.run_module('llmtool._git_supervisor', run_name='__main__')
            self.assertEqual(caught.exception.code, 78)
            with self.assertRaises(ImportError):
                runpy.run_path(str(PACKAGE / '_git_supervisor.py'), run_name='__main__')
            spawn.assert_not_called()

    def test_arbitrary_host_rejected_before_spawn(self):
        with patch.object(subprocess, 'Popen') as spawn:
            with self.assertRaises(kernel.NotSupported):
                containment._run(containment._Host(pinned(), 'a'*64))
            spawn.assert_not_called()

    def test_supervisor_bad_code_handshake_rejects_before_ready_and_spawn(self):
        module = importlib.import_module('llmtool._git_supervisor')
        own = kernel.Identity(123, 123, 123, os.geteuid(), 124, 1, 2)
        parent = replace(own, pid=124, pgid=124, sid=124, ppid=125)
        start = wire.message('START', nonce=NONCE, case='exit', timeout=1,
            parent=parent.data(), launch=own.data(), binding={'untrusted': 'manifest'},
            pipes=[[1, 2], [3, 4]])
        reader = Mock()
        reader.receive.return_value = start
        with patch.object(module.sys, 'flags', SimpleNamespace(isolated=1)), \
                patch.object(module.sys, 'argv', ['internal']), \
                patch.object(module, 'host_check'), \
                patch.object(module.os, 'getpid', return_value=123), \
                patch.object(module.os, 'getppid', return_value=124), \
                patch.object(module, 'identity', side_effect=lambda pid: own if pid == 123 else parent), \
                patch.object(module, '_endpoints', return_value=((5, os.O_RDONLY, [1, 2]),
                                                                 (6, os.O_WRONLY, [3, 4]))), \
                patch.object(wire, 'Reader', return_value=reader), \
                patch.object(wire, 'send') as send, \
                patch.object(subprocess, 'Popen') as spawn:
            self.assertEqual(module._main(), 78)
            spawn.assert_not_called()
            send.assert_not_called()

    def test_second_binding_check_rejects_drift_before_child_spawn(self):
        module = importlib.import_module('llmtool._git_supervisor')
        own = kernel.Identity(123, 123, 123, os.geteuid(), 124, 1, 2)
        parent = replace(own, pid=124, pgid=124, sid=124, ppid=125)
        binding = {'fixture': {'case': 'exit', 'timeout': 1}}
        start = wire.message('START', nonce=NONCE, case='exit', timeout=1,
            parent=parent.data(), launch=own.data(), binding=binding,
            pipes=[[1, 2], [3, 4]])
        reader = Mock()
        reader.receive.return_value = start
        with patch.object(module.sys, 'flags', SimpleNamespace(isolated=1)), \
                patch.object(module.sys, 'argv', ['internal']), \
                patch.object(module, 'host_check'), \
                patch.object(module.os, 'getpid', return_value=123), \
                patch.object(module.os, 'getppid', return_value=124), \
                patch.object(module, 'identity', side_effect=lambda pid: own if pid == 123 else parent), \
                patch.object(module, '_endpoints', return_value=((5, os.O_RDONLY, [1, 2]),
                                                                 (6, os.O_WRONLY, [3, 4]))), \
                patch.object(wire, 'Reader', return_value=reader), \
                patch.object(wire, 'verify_binding', side_effect=(NONCE, wire.ProtocolFailure())), \
                patch.object(module.signal, 'signal'), \
                patch.object(wire, 'send') as send, \
                patch.object(subprocess, 'Popen') as spawn:
            self.assertEqual(module._main(), 78)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[1]['type'], 'READY')
            spawn.assert_not_called()

    def test_fake_child_direct_wrapper_missing_handshake_cannot_spawn(self):
        module = importlib.import_module('llmtool._git_containment_child')
        with patch.object(module.sys, 'flags', SimpleNamespace(isolated=1)), \
                patch.object(module.sys, 'argv', ['internal', 'descendant', 'leader']), \
                patch.object(subprocess, 'Popen') as spawn:
            self.assertEqual(module._main(), 78)
            spawn.assert_not_called()

    def test_post_start_replacement_and_wrong_cancel_nonce_reject(self):
        module = importlib.import_module('llmtool._git_supervisor')
        own = kernel.Identity(123, 123, 123, os.geteuid(), 124, 1, 2)
        parent = replace(own, pid=124, pgid=124, sid=124, ppid=125)
        member = replace(own, pid=456, ppid=own.pid)
        start = wire.message('START', nonce=NONCE, case='exit', timeout=1,
            parent=parent.data(), launch=own.data(),
            binding={'fixture': {'case': 'exit', 'timeout': 1}}, pipes=[[1, 2], [3, 4]])
        class Contained(BaseException):
            pass
        for before_spawn in (True, False):
            for pending in (start, {**start, 'timeout': 2},
                            wire.message('CANCEL', nonce='b'*64)):
                with self.subTest(before_spawn=before_spawn, kind=pending['type']):
                    reader, child, note = Mock(), Mock(pid=member.pid), Mock()
                    child.stdout.fileno.return_value = 8
                    child.stderr.fileno.return_value = 9
                    reader.receive.side_effect = [start, pending]
                    reader.read.return_value = pending
                    selections = [([5], [], [])] if before_spawn else [([], [], []), ([5], [], [])]
                    with patch.object(module.sys, 'flags', SimpleNamespace(isolated=1)), \
                            patch.object(module.sys, 'argv', ['internal']), \
                            patch.object(module, 'host_check'), \
                            patch.object(module.os, 'getpid', return_value=own.pid), \
                            patch.object(module.os, 'getppid', return_value=parent.pid), \
                            patch.object(module, 'identity', side_effect=lambda pid: {
                                own.pid: own, parent.pid: parent, member.pid: member}[pid]), \
                            patch.object(module, '_endpoints', return_value=((5, os.O_RDONLY, [1, 2]),
                                                                            (6, os.O_WRONLY, [3, 4]))), \
                            patch.object(wire, 'Reader', return_value=reader), \
                            patch.object(wire, 'verify_binding', return_value=NONCE), \
                            patch.object(wire, 'send'), \
                            patch.object(module.signal, 'signal'), \
                            patch.object(module.select, 'select', side_effect=selections), \
                            patch.object(module.os, 'set_blocking'), \
                            patch.object(module, 'ExitNote', return_value=note), \
                            patch.object(module, '_last_act', side_effect=Contained) as cleanup, \
                            patch.object(subprocess, 'Popen', return_value=child) as spawn:
                        if before_spawn:
                            self.assertEqual(module._main(), 78)
                            spawn.assert_not_called()
                            cleanup.assert_not_called()
                        else:
                            with self.assertRaises(Contained):
                                module._main()
                            spawn.assert_called_once()
                            cleanup.assert_called_once_with(own)
                            child.wait.assert_not_called()
                            child.poll.assert_not_called()
                            child.communicate.assert_not_called()
                            note.close.assert_called_once()

    def test_f1_retains_primary_and_ownership_without_restoring(self):
        ownership = {'signals': Mock(), 'process': Mock(), 'fds': [9, 10]}
        error = containment.ContainmentUnresolved('interrupted', ownership)
        try:
            self.assertEqual(error.primary, 'interrupted')
            self.assertIs(error.ownership, ownership)
            ownership['signals'].restore.assert_not_called()
            ownership['process'].wait.assert_not_called()
            self.assertIn(error, containment._UNRESOLVED)
        finally:
            containment._UNRESOLVED.remove(error)


class UnresolvedReportTests(unittest.TestCase):
    def test_report_loss_retains_operator_wait_with_unavailable_supervisor(self):
        ns = runpy.run_path(str(HOST), run_name='remediation_test')
        class Waiting(BaseException):
            pass
        for failure in (BrokenPipeError(errno.EPIPE, 'closed'),
                        OSError(errno.EBADF, 'invalid'), RuntimeError('report failure')):
            with self.subTest(failure=type(failure).__name__):
                ownership = {'anchor': None, 'child': {}, 'process': Mock(returncode=77),
                             'fds': [9, 10], 'signals': Mock(), 'monitor': Mock(),
                             'runtime': Mock(), 'primary_exception': OSError('original')}
                exc = containment.ContainmentUnresolved('supervisor-lost', ownership)
                def wait(*args):
                    self.assertIs(exc.ownership, ownership)
                    self.assertIs(exc.primary_exception, ownership['primary_exception'])
                    self.assertIn(exc, containment._UNRESOLVED)
                    for key, method in (('process', 'wait'), ('signals', 'restore'),
                                        ('monitor', 'close'), ('runtime', 'cleanup')):
                        getattr(ownership[key], method).assert_not_called()
                    self.assertEqual(ownership['fds'], [9, 10])
                    raise Waiting
                try:
                    with patch.object(sys, 'argv', [str(HOST)]), \
                            patch.object(wire, 'file_hash', return_value='0'*64), \
                            patch.object(containment, '_run', side_effect=exc), \
                            patch.object(containment.os, 'write', side_effect=failure) as write, \
                            patch.object(ns['select'], 'select', side_effect=wait) as waiting:
                        with self.assertRaises(Waiting):
                            ns['main']()
                        write.assert_called_once()
                        waiting.assert_called_once_with([], [], [], 1)
                        self.assertEqual(exc.primary, 'supervisor-lost')
                finally:
                    containment._UNRESOLVED.remove(exc)


class ParentLifecycleTests(unittest.TestCase):
    def exercise(self, unresolved, terminal_failure=None, close_fault=False):
        parent = kernel.Identity(os.getpid(), os.getpid(), os.getpid(), os.geteuid(), 1, 1, 2)
        bound = kernel.Identity(123, 123, 123, os.geteuid(), os.getpid(), 1, 2)
        process = Mock(pid=123, returncode=None)
        monitor = Mock()
        monitor.exited.return_value = False
        if close_fault:
            monitor.close.side_effect = OSError('synthetic undisclosed exception text')
        anchor = Mock(bound=bound, signal_faults=[], monitor_faults=[], verification=None)
        signals = Mock(first=None, first_time=None)
        neutral = tempfile.TemporaryDirectory()
        real_pipe, fds, calls = os.pipe, [], []
        def pipe():
            self.assertEqual(calls[0], 'install')
            ends = real_pipe()
            fds.extend(ends)
            return ends
        def launch(argv, **kwargs):
            self.assertEqual(argv, [sys.executable, '-I', '-m', 'llmtool._git_supervisor'])
            self.assertEqual(kwargs['pass_fds'], (fds[0], fds[3]))
            self.assertTrue(kwargs['close_fds'] and kwargs['start_new_session'])
            self.assertFalse(kwargs['shell'])
            self.assertEqual(kwargs['env'], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
            self.assertTrue(all(not os.get_inheritable(fd) for fd in fds))
            self.assertNotIn(str(PACKAGE), kwargs['cwd'])
            calls.append('spawn')
            return process
        def send(*args):
            for fd in (fds[0], fds[3]):
                with self.assertRaises(OSError):
                    os.fstat(fd)  # Parent supervisor-only endpoints closed immediately.
            if terminal_failure is None:
                signals.first = signal.SIGTERM
                signals.first_time = time.monotonic()
                raise BrokenPipeError('containment peer closed')
        signals.install.side_effect = lambda: calls.append('install')
        def restore():
            for fd in fds:
                with self.assertRaises(OSError):
                    os.fstat(fd)
            self.assertEqual(calls[-1], 'gone')
            calls.append('restore')
        signals.restore.side_effect = restore
        def kill_group(*, deadline):
            self.assertLessEqual(deadline-time.monotonic(), 5)
            if terminal_failure is not None:
                signals.first = signal.SIGTERM  # Secondary interruption during cleanup.
                signals.first_time = time.monotonic()
            if unresolved:
                raise kernel.IdentityUnknown('synthetic ambiguity')
            calls.append('kill')
        anchor.kill_group.side_effect = kill_group
        anchor.reap.side_effect = lambda deadline: calls.append('reap')
        def gone(*args):
            calls.append('gone')
            return True
        reader = Mock()
        if terminal_failure is not None:
            child = replace(bound, pid=456, ppid=bound.pid).data()
            reader.read.side_effect = [
                wire.message('READY', nonce=NONCE, digest=NONCE, supervisor=bound.data()),
                wire.message('CHILD_IDENTITY', nonce=NONCE, child=child),
                wire.message('TERMINAL', nonce=NONCE, outcome=terminal_failure, evidence={})]
        try:
            with patch.object(containment, '_validate_host'), \
                    patch.object(wire, 'runtime_binding', return_value={}), \
                    patch.object(wire, 'verify_binding', return_value=NONCE), \
                    patch.object(containment, 'identity', side_effect=lambda pid: parent if pid == os.getpid() else bound), \
                    patch.object(containment, 'Anchor', return_value=anchor), \
                    patch.object(containment, 'ExitNote', return_value=monitor), \
                    patch.object(containment, '_Signals', return_value=signals), \
                    patch.object(containment.os, 'pipe', side_effect=pipe), \
                    patch.object(containment.tempfile, 'TemporaryDirectory', return_value=neutral), \
                    patch.object(wire, 'send', side_effect=send), \
                    patch.object(wire, 'Reader', return_value=reader), \
                    patch.object(containment, '_event'), \
                    patch.object(containment.os, 'urandom', return_value=bytes.fromhex(NONCE)), \
                    patch.object(subprocess, 'Popen', side_effect=launch) as spawn, \
                    patch.object(containment, '_group_gone', side_effect=gone):
                if unresolved:
                    with self.assertRaises(containment.ContainmentUnresolved) as caught:
                        containment._run(containment._Host({}, NONCE))
                    error = caught.exception
                    self.assertEqual(error.primary, terminal_failure or 'interrupted')
                    self.assertIs(error.ownership['process'], process)
                    self.assertIs(error.ownership['runtime'], neutral)
                    self.assertEqual(error.diagnostics['cleanup_outcome'], 'unresolved')
                    self.assertEqual(error.diagnostics['cleanup_errors'][0]['exception_type'], 'IdentityUnknown')
                    self.assertEqual(error.diagnostics['cleanup_errors'][0]['message'], 'details withheld')
                    if terminal_failure is None:
                        self.assertIsInstance(error.primary_exception, BrokenPipeError)
                        self.assertEqual(error.diagnostics['primary_error']['message'], 'containment peer closed')
                        self.assertEqual(error.diagnostics['primary_error']['exception_type'], 'BrokenPipeError')
                    self.assertEqual(error.diagnostics['timing'], 'ordinary')
                    self.assertEqual(error.diagnostics['signal'], signal.SIGTERM)
                    self.assertEqual(error.diagnostics['expected_event'], 'group-decision')
                    self.assertEqual(len(error.ownership['fds']), 2)
                    for fd in error.ownership['fds']:
                        os.fstat(fd)  # Ownership and endpoints remain retained.
                    signals.restore.assert_not_called()
                    anchor.reap.assert_not_called()
                    monitor.close.assert_not_called()
                    containment._UNRESOLVED.remove(error)
                else:
                    outcome = containment._run(containment._Host({}, NONCE))
                    self.assertEqual(outcome.primary, terminal_failure or 'interrupted')
                    self.assertTrue(outcome.containment_confirmed)
                    self.assertEqual(outcome.cleanup_faults, ('monitor-close',) if close_fault else ())
                    if close_fault:
                        self.assertEqual(outcome.diagnostics['cleanup_errors'][0]['message'], 'details withheld')
                        self.assertNotIn(b'synthetic undisclosed', wire.canonical(outcome.diagnostics))
                    self.assertEqual(calls, ['install', 'spawn', 'kill', 'reap', 'gone', 'restore'])
                spawn.assert_called_once()
                process.wait.assert_not_called()
                process.poll.assert_not_called()
                process.communicate.assert_not_called()
        finally:
            # Only unit-owned fixture FDs, never a live process or user file.
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
            neutral.cleanup()

    def test_fd_launch_cleanup_order_and_epipe_preserve_interruption(self):
        self.exercise(False)

    def test_f1_parent_control_flow_retains_resources_and_primary(self):
        self.exercise(True)

    def test_primary_failure_survives_signal_during_cleanup(self):
        for failure in ('timeout', 'output-limit', 'protocol-failure', 'supervisor-lost',
                        'start-failed'):
            with self.subTest(failure=failure):
                self.exercise(False, failure)

    def test_primary_timeout_survives_unresolved_cleanup_and_signal(self):
        self.exercise(True, 'timeout')

    def test_primary_timeout_survives_monitor_close_fault_without_exception_text(self):
        self.exercise(False, 'timeout', close_fault=True)


class Probe:
    """Outer acceptance owner, never a callback within the engine window."""
    def __init__(self, case='descendant', *, hold=False, popen=False, timing='ordinary', timeout=2):
        self.timing, self.signal_number = timing, None
        self.subcase = case
        self.fixture_cleanup = None
        self.expected_event = 'launch'
        self.cleanup_outcome, self.cleanup_errors = 'not-started', []
        argv = [sys.executable, '-I', str(HOST), '--case', case,
                '--timing', timing, '--timeout', str(timeout)]
        if hold:
            argv.append('--hold')
        if popen:
            argv.append('--popen')
        self.process = subprocess.Popen(argv, shell=False, close_fds=True,
            start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=wire.fresh_environment(), cwd=str(HOST.parents[3]))
        self.buffer, self.rows = bytearray(), []
        self.supervisor = None
        self.reported_unresolved_supervisor = None
        self.host_identity = None
        self.members = {}
        self.unbound_supervisor_pid = None
        try:
            self.host_identity = kernel.identity(self.process.pid)
            os.set_blocking(self.process.stdout.fileno(), False)
        except BaseException as error:
            _FAILED_PROBES.append(self)  # Never release an ambiguous launch pin.
            error.probe_diagnostics = self.diagnostics(error)
            error.fixture_cleanup = {'status': 'UNRESOLVED',
                                     'original_owned_processes': [self.inventory()],
                                     'evidence': {'host_reaped': False, 'host_absent': False}}
            raise

    def next(self, deadline, allow_unresolved=False):
        while time.monotonic() < deadline:
            heartbeat()
            if b'\n' in self.buffer:
                raw, _, rest = self.buffer.partition(b'\n')
                self.buffer = bytearray(rest)
                row = wire.strict_load(bytes(raw))
                if type(row) is not dict or type(row.get('event')) is not str:
                    wire.reject()
                if len(self.rows) >= 16:
                    raise AssertionError('probe event count limit')
                self.rows.append(row)
                if row.get('supervisor'):
                    reported = kernel.Identity.parse(row['supervisor'])
                    if self.supervisor is None:
                        self.supervisor = reported  # Normally the authoritative READY identity.
                    elif any(getattr(reported, key) != getattr(self.supervisor, key)
                             for key in ('pid', 'pgid', 'sid', 'uid', 'ppid', 'seconds')):
                        raise AssertionError('probe supervisor identity mismatch')
                    elif row['event'] != 'unresolved' and reported != self.supervisor:
                        raise AssertionError('probe supervisor identity mismatch')
                    # F1 reports its deliberately faulted anchor. Preserve the
                    # real launch binding for fresh identity checks/inventory.
                    if row['event'] == 'unresolved':
                        self.reported_unresolved_supervisor = reported
                    self.unbound_supervisor_pid = None
                if row['event'] == 'spawn':
                    self.unbound_supervisor_pid = row['supervisor_pid']
                if row.get('child'):
                    item = kernel.Identity.parse(row['child'])
                    self.members[item.pid] = item
                if row.get('evidence'):
                    wire.child_evidence(row['evidence'])
                    for data in row['evidence']['descendants']:
                        item = kernel.Identity.parse(data)
                        self.members[item.pid] = item
                if row['event'] == 'stress':
                    item = kernel.Identity.parse(row['identity'])
                    self.members[item.pid] = item
                if len(self.members) > 4:
                    raise AssertionError('probe event count limit')
                if (row.get('event') == 'unsupported'
                        or row.get('event') == 'unresolved' and not allow_unresolved):
                    raise AssertionError('mandatory Mac capability unsupported or unresolved')
                return row
            try:
                chunk = os.read(self.process.stdout.fileno(), 65536)
            except BlockingIOError:
                chunk = None
            if chunk == b'':
                raise AssertionError('probe parent closed before expected evidence')
            if chunk:
                self.buffer.extend(chunk)
                if len(self.buffer) > wire.LIMIT:
                    raise AssertionError('probe evidence output limit')
            select.select([self.process.stdout], [], [], .005)
        raise AssertionError('probe evidence deadline')

    def until(self, kind, seconds=5):
        self.expected_event = kind
        deadline = time.monotonic()+seconds
        while True:
            row = self.next(deadline, allow_unresolved=(kind == 'unresolved'))
            if row['event'] == kind:
                return row

    def reap(self):
        result = self.process.wait(timeout=6)
        self.process.stdout.close()
        return result

    def inventory(self):
        result = {'pid': self.process.pid,
                  'host_identity': self.host_identity.data() if self.host_identity else {},
                  'supervisor': self.supervisor.data() if self.supervisor else {},
                  'members': [item.data() for item in self.members.values()]}
        if self.unbound_supervisor_pid is not None:
            result['unbound_supervisor_pid'] = self.unbound_supervisor_pid
        return result

    def cleanup(self, deadline=None):
        # Test controller only: production F1 must retain ownership indefinitely.
        # Kill only our real unreaped host under fresh launch continuity. EOF
        # then invokes the supervisor's owned-group path. Grandchild/member
        # snapshots permit passive absence checks, never numeric signaling.
        original = self.inventory()  # Freeze before kill/reap/closing anything.
        proof = {'host_pin_verified': False, 'host_signal': None,
                 'decision_before_reap': False, 'host_reaped': False,
                 'host_absent': False, 'supervisor_absent': False,
                 'group_absent': False, 'members_absent': [], 'inspection_eperm': False}
        self.fixture_cleanup = {'status': 'UNRESOLVED',
                                'original_owned_processes': [original], 'evidence': proof}
        note = None
        failure = None
        self.cleanup_outcome = 'pending'
        limit = time.monotonic()+6  # Existing controller bound, never restarted.
        deadline = limit if deadline is None else min(deadline, limit)
        try:
            if self.process.returncode is None:
                if self.host_identity is None:
                    raise kernel.IdentityUnknown('probe host ownership unknown')
                owner = kernel.Anchor(self.process, self.host_identity)
                owner.verify(deadline=deadline)
                note = kernel.ExitNote(self.process.pid)
                owner.verify(deadline=deadline)  # After registration, still unreaped.
                proof['host_pin_verified'] = True
                try:
                    os.kill(self.process.pid, signal.SIGKILL)  # Only our pinned host.
                    proof['host_signal'] = 'SIGKILL'
                except ProcessLookupError:
                    owner.verify(deadline=deadline)  # Fresh owned-zombie proof, no retry.
                    proof['host_signal'] = 'ESRCH-owned'
                owner.decided = True  # Host-only decision; no group signal in this controller.
                proof['decision_before_reap'] = True
                while not note.exited():
                    heartbeat()
                    if time.monotonic() >= deadline:
                        raise kernel.IdentityUnknown('probe host retained unresolved ownership')
                    select.select([], [], [], min(.005, max(0, deadline-time.monotonic())))
                owner.reap(deadline)
            proof['host_reaped'] = self.process.returncode is not None
            # Reaped IDs are only inspected. Missing supervisor identity is
            # ambiguous unless the actual host result proves no launch occurred.
            no_launch = any(row['event'] == 'result' and row.get('supervisor') == {}
                            and row.get('containment_confirmed') is True for row in self.rows)
            if self.unbound_supervisor_pid is not None or (self.supervisor is None and not no_launch):
                raise kernel.IdentityUnknown('probe group disappearance unconfirmed')
            def inspected_absent(pid, group=False):
                try:
                    return kernel.absent(pid, group=group)
                except PermissionError:
                    # Passive inspection is inconclusive, never absence. Keep
                    # polling within the same deadline without another signal.
                    proof['inspection_eperm'] = True
                    return False
            while time.monotonic() < deadline:
                heartbeat()
                proof['host_absent'] = inspected_absent(self.process.pid)
                proof['supervisor_absent'] = (self.supervisor is None
                                              or inspected_absent(self.supervisor.pid))
                proof['group_absent'] = (self.supervisor is None
                                         or inspected_absent(self.supervisor.pgid, group=True))
                proof['members_absent'] = [{'pid': item.pid, 'absent': inspected_absent(item.pid)}
                                           for item in self.members.values()]
                if (proof['host_absent'] and proof['supervisor_absent'] and proof['group_absent']
                        and all(item['absent'] for item in proof['members_absent'])):
                    break
                select.select([], [], [], min(.005, max(0, deadline-time.monotonic())))
            else:
                raise kernel.IdentityUnknown('probe group disappearance unconfirmed')
            self.process.stdout.close()
            self.cleanup_outcome = 'confirmed'
        except BaseException as error:
            failure = error
            self.cleanup_errors.append(wire.exception_evidence(error))
            self.cleanup_outcome = 'unresolved'
            if self not in _FAILED_PROBES:
                _FAILED_PROBES.append(self)
        finally:
            if note is not None:
                try:
                    note.close()
                except BaseException as error:
                    self.cleanup_errors.append(wire.exception_evidence(error))
                    if failure is None:
                        failure = error
                        self.cleanup_outcome = 'monitor-close-failed'
        if failure is not None:
            if self not in _FAILED_PROBES:
                _FAILED_PROBES.append(self)
            failure.fixture_cleanup = self.fixture_cleanup
            raise failure
        self.fixture_cleanup['status'] = 'RESOLVED'
        if self in _FAILED_PROBES:
            _FAILED_PROBES.remove(self)
        return self.fixture_cleanup

    def diagnostics(self, primary):
        # Project only known synthetic metadata. Never include stderr, raw pipe
        # fragments, arbitrary event keys, environment or argv in failure JSON.
        rows = []
        for row in self.rows[-16:]:
            if type(row) is not dict:
                rows.append({'event': 'invalid'})
                continue
            event = row.get('event')
            if event not in ('ready', 'child', 'terminal', 'result', 'unresolved',
                             'pre_pipe', 'spawn', 'cleanup', 'handlers_restored',
                             'stress', 'unsupported'):
                rows.append({'event': 'unknown'})
                continue
            item = {'event': event}
            for key in ('supervisor', 'child', 'identity'):
                if row.get(key):
                    try:
                        item[key] = kernel.Identity.parse(row[key]).data()
                    except kernel.IdentityUnknown:
                        item[key] = {'invalid_identity': True}
            if type(row.get('primary')) is str and row['primary'] in wire.OUTCOMES:
                item['primary'] = row['primary']
            for key in ('containment_confirmed', 'restored', 'user_action_required'):
                if type(row.get(key)) is bool:
                    item[key] = row[key]
            if event in ('unresolved', 'result'):
                # These diagnostics originate solely in the reviewed bound host,
                # whose exception formatter already withholds non-fixed text.
                item['diagnostics'] = row.get('diagnostics', {})
            rows.append(item)
        return {'subcase': self.subcase, 'timing': self.timing, 'signal': self.signal_number,
                'expected_event': self.expected_event,
                'primary_error': wire.exception_evidence(primary),
                'event_rows': rows, 'cleanup_outcome': self.cleanup_outcome,
                'cleanup_errors': self.cleanup_errors[-4:]}


@contextmanager
def probe_scope(probe):
    """A cleanup/monitor error cannot replace the actual acceptance failure."""
    primary = None
    original_diagnostics = None
    try:
        yield probe
    except BaseException as error:
        primary = error
        original_diagnostics = probe.diagnostics(error)  # Freeze the exact failing subcase/error first.
        error.primary_before_cleanup = original_diagnostics['primary_error']
        raise
    finally:
        try:
            probe.cleanup()
        except BaseException as cleanup:
            if primary is None:
                cleanup.probe_diagnostics = probe.diagnostics(cleanup)
                cleanup.fixture_cleanup = probe.fixture_cleanup
                raise
            # The pending original raise survives; ownership is retained by
            # cleanup on ambiguity. Keep the secondary failure separately.
        if primary is not None:
            primary.probe_diagnostics = {**original_diagnostics,
                                         'cleanup_outcome': probe.cleanup_outcome,
                                         'cleanup_errors': probe.cleanup_errors[-4:]}
            primary.fixture_cleanup = probe.fixture_cleanup


class ProbeDiagnosticsTests(unittest.TestCase):
    def probe(self):
        probe = Probe.__new__(Probe)  # Deterministic fake; no subprocess launch.
        probe.process = Mock(spec=subprocess.Popen, pid=123, returncode=None)
        probe.process.stdout = Mock()
        probe.host_identity = kernel.Identity(123, 123, 123, os.geteuid(), os.getpid(), 1, 2)
        probe.supervisor = None
        probe.reported_unresolved_supervisor = None
        probe.rows = [{'event': 'terminal', 'primary': 'complete',
                       'unreviewed': 'SYNTHETIC_SECRET'}]
        probe.timing, probe.signal_number = 'ordinary', int(signal.SIGINT)
        probe.expected_event = 'result'
        probe.cleanup_outcome, probe.cleanup_errors = 'not-started', []
        probe.subcase, probe.fixture_cleanup = 'closed_status', None
        probe.members, probe.unbound_supervisor_pid = {}, None
        return probe

    @staticmethod
    def feed(probe, *rows):
        probe.buffer = bytearray(b''.join(wire.canonical(row)+b'\n' for row in rows))
        with patch.object(sys.modules[__name__], 'heartbeat'):
            return [probe.next(time.monotonic()+1, allow_unresolved=True) for _ in rows]

    def test_ready_binding_and_inventory_survive_unresolved_anchor_fault(self):
        probe = self.probe()
        probe.rows = []
        bound = kernel.Identity(456, 456, 456, os.geteuid(), 123, 1, 999999)
        faulted = replace(bound, micros=(bound.micros+1) % 1000000)
        ready = {'event': 'ready', 'supervisor': bound.data()}
        terminal = {'event': 'terminal', 'supervisor': bound.data()}
        unresolved = {'event': 'unresolved', 'supervisor': faulted.data()}
        self.assertEqual(self.feed(probe, ready, terminal, unresolved), [ready, terminal, unresolved])
        self.assertEqual(probe.supervisor, bound)
        self.assertEqual(probe.reported_unresolved_supervisor, faulted)
        self.assertEqual(probe.inventory()['supervisor'], bound.data())
        self.assertEqual(probe.rows, [ready, terminal, unresolved])
        probe.process.wait.assert_not_called()

    def test_terminal_and_result_keep_ready_identity_and_reject_micros_drift(self):
        bound = kernel.Identity(456, 456, 456, os.geteuid(), 123, 1, 3)
        for event in ('terminal', 'result', 'ready'):
            with self.subTest(event=event):
                probe = self.probe()
                self.feed(probe, {'event': 'ready', 'supervisor': bound.data()},
                          {'event': event, 'supervisor': bound.data()})
                self.assertEqual(probe.supervisor, bound)
                self.assertIsNone(probe.reported_unresolved_supervisor)
                with self.assertRaisesRegex(AssertionError, 'probe supervisor identity mismatch'):
                    self.feed(probe, {'event': event,
                                     'supervisor': replace(bound, micros=4).data()})
                self.assertEqual(probe.inventory()['supervisor'], bound.data())

    def test_later_supervisor_rows_reject_non_micros_identity_drift(self):
        bound = kernel.Identity(456, 456, 456, os.geteuid(), 123, 1, 3)
        for event in ('unresolved', 'terminal', 'result'):
            for key in ('pid', 'pgid', 'sid', 'uid', 'ppid', 'seconds'):
                with self.subTest(event=event, key=key):
                    probe = self.probe()
                    self.feed(probe, {'event': 'ready', 'supervisor': bound.data()})
                    drifted = replace(bound, **{key: getattr(bound, key)+1})
                    with self.assertRaisesRegex(AssertionError, 'probe supervisor identity mismatch'):
                        self.feed(probe, {'event': event, 'supervisor': drifted.data()})
                    self.assertEqual(probe.supervisor, bound)
                    self.assertEqual(probe.inventory()['supervisor'], bound.data())
                    self.assertIsNone(probe.reported_unresolved_supervisor)

    def test_first_supervisor_row_still_binds_without_ready(self):
        probe = self.probe()
        bound = kernel.Identity(456, 456, 456, os.geteuid(), 123, 1, 3)
        self.feed(probe, {'event': 'unresolved', 'supervisor': bound.data()})
        self.assertEqual(probe.supervisor, bound)
        self.assertEqual(probe.reported_unresolved_supervisor, bound)
        self.assertEqual(probe.inventory()['supervisor'], bound.data())

    def test_primary_signal_path_error_survives_cleanup_and_monitor_errors(self):
        probe = self.probe()
        primary = AssertionError('mandatory Mac capability unsupported or unresolved')
        cleanup = kernel.IdentityUnknown('probe host retained unresolved ownership')
        note, owner = Mock(), Mock()
        note.exited.side_effect = cleanup
        note.close.side_effect = OSError(errno.EPERM, 'SYNTHETIC_SECRET')
        with patch.object(kernel, 'Anchor', return_value=owner), \
                patch.object(kernel, 'ExitNote', return_value=note), \
                patch.object(kernel, '_info', return_value=SimpleNamespace(status=2)), \
                patch.object(os, 'kill'), patch.object(sys.modules[__name__], 'heartbeat'):
            with self.assertRaises(AssertionError) as caught:
                with probe_scope(probe):
                    raise primary
        try:
            self.assertIs(caught.exception, primary)
            data = primary.probe_diagnostics
            self.assertEqual(data['primary_error']['message'], str(primary))
            self.assertTrue(data['primary_error']['traceback'])
            self.assertEqual(data['timing'], 'ordinary')
            self.assertEqual(data['signal'], int(signal.SIGINT))
            self.assertEqual(data['expected_event'], 'result')
            self.assertEqual(data['event_rows'], [{'event': 'terminal', 'primary': 'complete'}])
            self.assertEqual(data['cleanup_outcome'], 'unresolved')
            self.assertEqual([row['exception_type'] for row in data['cleanup_errors']],
                             ['IdentityUnknown', 'PermissionError'])
            self.assertNotIn(b'SYNTHETIC_SECRET', wire.canonical(data))
            self.assertIn(probe, _FAILED_PROBES)
            probe.process.wait.assert_not_called()
            owner.kill_group.assert_not_called()
        finally:
            _FAILED_PROBES.remove(probe)

    def test_cleanup_error_alone_is_not_a_success_or_generic_replacement(self):
        probe = self.probe()
        cleanup = kernel.IdentityUnknown('probe host ownership unknown')
        owner = Mock()
        owner.verify.side_effect = cleanup
        with patch.object(kernel, 'Anchor', return_value=owner):
            with self.assertRaises(kernel.IdentityUnknown) as caught:
                with probe_scope(probe):
                    pass
        try:
            self.assertIs(caught.exception, cleanup)
            self.assertEqual(cleanup.probe_diagnostics['cleanup_outcome'], 'unresolved')
            probe.process.wait.assert_not_called()
            owner.kill_group.assert_not_called()
        finally:
            _FAILED_PROBES.remove(probe)

    def test_protocol_error_retained_when_cleanup_succeeds(self):
        probe = self.probe()
        primary = wire.ProtocolFailure('invalid containment protocol')
        def cleaned():
            probe.cleanup_outcome = 'confirmed'
        with patch.object(probe, 'cleanup', side_effect=cleaned):
            with self.assertRaises(wire.ProtocolFailure) as caught:
                with probe_scope(probe):
                    raise primary
        self.assertIs(caught.exception, primary)
        self.assertEqual(primary.probe_diagnostics['cleanup_errors'], [])
        self.assertEqual(primary.probe_diagnostics['cleanup_outcome'], 'confirmed')

    def test_exception_evidence_withholds_secret_messages_paths_argv_and_output(self):
        errors = (RuntimeError('SYNTHETIC_SECRET'),
                  OSError(errno.ENOENT, 'SYNTHETIC_SECRET', '/private/SYNTHETIC_SECRET'),
                  subprocess.TimeoutExpired(['SYNTHETIC_SECRET'], 1,
                                            output=b'SYNTHETIC_SECRET', stderr=b'SYNTHETIC_SECRET'))
        for error in errors:
            try:
                raise error
            except BaseException as caught:
                data = wire.exception_evidence(caught)
            self.assertLessEqual(len(data['traceback']), 8)
            self.assertNotIn(b'SYNTHETIC_SECRET', wire.canonical(data))
            self.assertTrue(data['message'])

    def test_fixture_backstop_and_note_close_cannot_mask_pending_native_assertion(self):
        primary = AssertionError('containment acceptance assertion failed')
        def denied():
            raise PermissionError(errno.EPERM, 'SYNTHETIC_SECRET')
        def monitor_fault():
            raise RuntimeError('SYNTHETIC_SECRET')
        with self.assertRaises(AssertionError) as caught:
            try:
                raise primary
            finally:
                cleanup_preserving_failure(denied)
                cleanup_preserving_failure(monitor_fault)
        self.assertIs(caught.exception, primary)
        self.assertEqual([row['exception_type'] for row in primary.secondary_cleanup_errors],
                         ['PermissionError', 'RuntimeError'])
        self.assertNotIn(b'SYNTHETIC_SECRET', wire.canonical(primary.secondary_cleanup_errors))


class ControllerCleanupTests(unittest.TestCase):
    """Real controller/Anchor flow with deterministic process and kernel models."""
    def setUp(self):
        self.probe = ProbeDiagnosticsTests().probe()
        self.probe.supervisor = kernel.Identity(456, 456, 456, os.geteuid(), 123, 1, 3)
        self.probe.members = {457: kernel.Identity(457, 456, 456, os.geteuid(), 456, 1, 4)}
        self.probe.rows = [{'event': 'ready', 'supervisor': self.probe.supervisor.data()}]
        self.events = []

    def tearDown(self):
        if self.probe in _FAILED_PROBES:
            _FAILED_PROBES.remove(self.probe)

    @contextmanager
    def model(self, *, note_exited=True, absent=True, identity_error=None, signal_error=None,
              observed_identity=None, inspection=None):
        probe = self.probe
        kernel_absent = kernel.absent
        note = Mock()
        note.exited.return_value = note_exited
        clock = [1.0]
        def observed(pid):
            self.assertEqual(pid, probe.process.pid)
            self.events.append('verify')
            if identity_error is not None:
                raise identity_error
            return observed_identity or probe.host_identity
        def signal_host(pid, number):
            self.assertEqual((pid, number), (123, signal.SIGKILL))
            self.assertIsNone(probe.process.returncode)
            probe.process.wait.assert_not_called()
            self.events.append('signal')
            if signal_error is not None:
                raise signal_error
        def reap(**kwargs):
            self.assertIn('signal', self.events)
            self.assertLessEqual(kwargs['timeout'], 6)
            self.events.append('reap')
            probe.process.returncode = -signal.SIGKILL
            return probe.process.returncode
        def inspect(pid, group=False):
            self.assertIsNotNone(probe.process.returncode)
            self.events.append('inspect')
            if inspection is not None:
                # Exercise real absent() translation with synthetic read-only
                # syscalls; no saved process ID is inspected or signaled.
                with patch.object(os, 'kill', side_effect=lambda pid, number:
                                  inspection(pid, number, False)), \
                        patch.object(os, 'killpg', side_effect=lambda pid, number:
                                     inspection(pid, number, True)):
                    return kernel_absent(pid, group=group)
            return absent
        def pause(*args):
            self.assertLessEqual(args[-1], .005)
            clock[0] += .25
        probe.process.wait.side_effect = reap
        with patch.object(kernel, 'host_check'), \
                patch.object(kernel, 'identity', side_effect=observed), \
                patch.object(kernel, 'ExitNote', return_value=note), \
                patch.object(kernel, 'absent', side_effect=inspect), \
                patch.object(os, 'kill', side_effect=signal_host) as host_signal, \
                patch.object(os, 'killpg') as group_signal, \
                patch.object(time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(select, 'select', side_effect=pause):
            yield note, host_signal, group_signal
        probe.process.poll.assert_not_called()
        probe.process.communicate.assert_not_called()

    def test_unresolved_fault_keeps_real_identity_in_frozen_cleanup_inventory(self):
        bound = self.probe.supervisor
        self.probe.supervisor = None
        self.probe.rows = []
        faulted = replace(bound, micros=bound.micros+1)
        ProbeDiagnosticsTests.feed(self.probe,
            {'event': 'ready', 'supervisor': bound.data()},
            {'event': 'unresolved', 'supervisor': faulted.data()})
        with self.model() as (_, host_signal, group_signal):
            cleanup = self.probe.cleanup()
        self.assertEqual(cleanup['status'], 'RESOLVED')
        self.assertEqual(cleanup['original_owned_processes'][0]['supervisor'], bound.data())
        self.assertEqual(self.probe.supervisor, bound)
        self.assertEqual(self.probe.reported_unresolved_supervisor, faulted)
        self.assertEqual(self.events[:4], ['verify', 'verify', 'signal', 'reap'])
        host_signal.assert_called_once_with(123, signal.SIGKILL)
        group_signal.assert_not_called()

    def test_original_assertion_inventory_and_subcase_survive_owned_host_cleanup(self):
        primary = AssertionError('containment acceptance assertion failed')
        original = self.probe.inventory()
        with self.model() as (note, host_signal, group_signal):
            with self.assertRaises(AssertionError) as caught:
                with probe_scope(self.probe):
                    raise primary
        self.assertIs(caught.exception, primary)
        self.assertEqual(primary.probe_diagnostics['subcase'], 'closed_status')
        self.assertEqual(primary.probe_diagnostics['primary_error']['message'], str(primary))
        self.assertTrue(primary.probe_diagnostics['primary_error']['traceback'])
        cleanup = primary.fixture_cleanup
        self.assertEqual(cleanup['original_owned_processes'], [original])
        self.assertEqual(cleanup['status'], 'RESOLVED')
        proof = cleanup['evidence']
        for key in ('host_pin_verified', 'decision_before_reap', 'host_reaped',
                    'host_absent', 'supervisor_absent', 'group_absent'):
            self.assertTrue(proof[key])
        self.assertEqual(proof['members_absent'], [{'pid': 457, 'absent': True}])
        self.assertEqual(self.events[:4], ['verify', 'verify', 'signal', 'reap'])
        host_signal.assert_called_once_with(123, signal.SIGKILL)
        group_signal.assert_not_called()
        note.close.assert_called_once()
        self.assertNotIn(self.probe, _FAILED_PROBES)

    def test_drift_permission_missing_identity_and_reaper_ambiguity_never_signal(self):
        for fault in ('drift', 'permission', 'missing', 'reaper'):
            with self.subTest(fault=fault):
                self.setUp()
                error = PermissionError(errno.EPERM, 'SYNTHETIC_SECRET') if fault == 'permission' else None
                observed = replace(self.probe.host_identity, micros=3) if fault == 'drift' else None
                if fault == 'missing':
                    self.probe.host_identity = None
                primary = AssertionError('containment acceptance assertion failed')
                with self.model(identity_error=error, observed_identity=observed) as (_, host_signal, group_signal), \
                        patch.object(kernel, 'host_check', side_effect=(
                            kernel.NotSupported('single-thread default-reaper host required')
                            if fault == 'reaper' else None)):
                    with self.assertRaises(AssertionError) as caught:
                        with probe_scope(self.probe):
                            raise primary
                self.assertIs(caught.exception, primary)
                self.assertEqual(primary.fixture_cleanup['status'], 'UNRESOLVED')
                self.assertTrue(primary.fixture_cleanup['original_owned_processes'])
                self.assertIn(self.probe, _FAILED_PROBES)
                self.probe.process.wait.assert_not_called()
                host_signal.assert_not_called()
                group_signal.assert_not_called()
                self.assertNotIn(b'SYNTHETIC_SECRET', wire.canonical(primary.probe_diagnostics))
                self.tearDown()

    def test_host_signal_permission_error_preserves_pin_without_reap_or_group_signal(self):
        with self.model(signal_error=PermissionError(errno.EPERM, 'private')) as (_, _, group):
            with self.assertRaises(PermissionError):
                self.probe.cleanup(deadline=2)
        self.assertEqual(self.probe.fixture_cleanup['status'], 'UNRESOLVED')
        self.assertIn(self.probe, _FAILED_PROBES)
        self.probe.process.wait.assert_not_called()
        group.assert_not_called()

    def test_unresponsive_host_deadline_preserves_primary_and_unreaped_inventory(self):
        primary = AssertionError('containment acceptance assertion failed')
        with self.model(note_exited=False) as (_, _, group):
            with self.assertRaises(AssertionError) as caught:
                with probe_scope(self.probe):
                    raise primary
        self.assertIs(caught.exception, primary)
        self.assertEqual(primary.fixture_cleanup['status'], 'UNRESOLVED')
        self.assertEqual(primary.probe_diagnostics['cleanup_outcome'], 'unresolved')
        self.probe.process.wait.assert_not_called()
        self.assertIn(self.probe, _FAILED_PROBES)
        group.assert_not_called()

    def test_retained_group_or_inspection_permission_is_not_resolved(self):
        for permission in (False, True):
            with self.subTest(permission=permission):
                self.setUp()
                with self.model(absent=False) as (_, signal_host, signal_group):
                    with patch.object(kernel, 'absent', return_value=False,
                                      side_effect=PermissionError(errno.EPERM, 'private') if permission else None):
                        with self.assertRaises((PermissionError, kernel.IdentityUnknown)):
                            self.probe.cleanup(deadline=2)
                self.assertEqual(self.probe.fixture_cleanup['status'], 'UNRESOLVED')
                self.assertTrue(self.probe.fixture_cleanup['evidence']['host_reaped'])
                self.assertIn(self.probe, _FAILED_PROBES)
                signal_host.assert_called_once_with(123, signal.SIGKILL)
                signal_group.assert_not_called()
                self.tearDown()

    def inspection_permission_case(self, persistent):
        targets = ((123, False), (456, False), (456, True), (457, False))
        for target in targets:
            with self.subTest(target=target):
                self.setUp()
                original = self.probe.inventory()
                observations = dict.fromkeys(targets, 0)
                def inspect(pid, number, group):
                    self.assertEqual(number, 0)  # Only passive absence checks.
                    self.assertIsNotNone(self.probe.process.returncode)
                    self.probe.process.wait.assert_called_once()
                    key = (pid, group)
                    observations[key] += 1
                    if key == target and (persistent or observations[key] == 1):
                        raise PermissionError(errno.EPERM, 'synthetic inspection denied')
                    raise ProcessLookupError(errno.ESRCH, 'synthetic process absent')
                with self.model(inspection=inspect) as (note, host_signal, group_signal):
                    if persistent:
                        with self.assertRaisesRegex(kernel.IdentityUnknown,
                                                    'probe group disappearance unconfirmed'):
                            self.probe.cleanup(deadline=2)
                        self.assertEqual(time.monotonic(), 2)
                    else:
                        self.probe.cleanup(deadline=2)
                cleanup = self.probe.fixture_cleanup
                self.assertEqual(cleanup['status'], 'UNRESOLVED' if persistent else 'RESOLVED')
                self.assertEqual(cleanup['original_owned_processes'], [original])
                proof = cleanup['evidence']
                self.assertTrue(proof['inspection_eperm'])
                self.assertTrue(proof['host_pin_verified'] and proof['decision_before_reap'])
                self.assertTrue(proof['host_reaped'])
                flags = (proof['host_absent'], proof['supervisor_absent'], proof['group_absent'],
                         proof['members_absent'][0]['absent'])
                for key, flag in zip(targets, flags):
                    self.assertEqual(flag, not (persistent and key == target))
                    self.assertGreaterEqual(observations[key], 2)
                self.assertEqual(self.events[:4], ['verify', 'verify', 'signal', 'reap'])
                host_signal.assert_called_once_with(123, signal.SIGKILL)
                group_signal.assert_not_called()
                note.close.assert_called_once()
                self.assertEqual(self.probe in _FAILED_PROBES, persistent)
                self.assertEqual(self.probe.cleanup_outcome, 'unresolved' if persistent else 'confirmed')
                self.tearDown()

    def test_transient_inspection_eperm_then_esrch_resolves_without_group_signal(self):
        self.inspection_permission_case(False)

    def test_persistent_inspection_eperm_expires_with_probe_retained_without_group_signal(self):
        self.inspection_permission_case(True)

    def test_released_host_is_only_inspected_and_unknown_supervisor_blocks(self):
        self.probe.process.returncode = 0
        with self.model() as (_, host_signal, group_signal):
            self.probe.cleanup(deadline=2)
        self.assertEqual(self.probe.fixture_cleanup['status'], 'RESOLVED')
        self.assertFalse(self.probe.fixture_cleanup['evidence']['host_pin_verified'])
        self.probe.process.wait.assert_not_called()
        host_signal.assert_not_called()
        group_signal.assert_not_called()
        self.probe.supervisor = None
        with self.model() as (_, host_signal, group_signal):
            with self.assertRaises(kernel.IdentityUnknown):
                self.probe.cleanup(deadline=2)
        self.assertEqual(self.probe.fixture_cleanup['status'], 'UNRESOLVED')
        self.probe.process.wait.assert_not_called()
        host_signal.assert_not_called()
        group_signal.assert_not_called()

    def test_subcase_progress_records_actual_entries_without_scenario_pass(self):
        scenarios = AcceptanceScenarios()
        scenarios.begin_subcase('hung')
        scenarios.finish_subcase()
        scenarios.begin_subcase('closed_status')
        self.assertEqual(scenarios.current_subcase, 'closed_status')
        self.assertEqual(scenarios.completed_subcases, ['hung'])
        self.assertEqual(scenarios.checks, 0)

    def test_failed_evidence_export_retains_assertion_source_hashes_and_original_inventory(self):
        # Exercise the actual --output failure writer, never a native success.
        import json
        runner = importlib.import_module('run_containment_acceptance')
        primary = AssertionError('containment acceptance assertion failed')
        with self.model():
            with self.assertRaises(AssertionError):
                with probe_scope(self.probe):
                    raise primary
        completed_cleanup = primary.fixture_cleanup
        primary.fixture_cleanup = None  # Later assertion, after its fixture scope has completed.
        scenarios = AcceptanceScenarios()
        def fail():
            scenarios.begin_subcase('closed_status')
            scenarios.last_fixture_cleanup = completed_cleanup
            raise primary
        scenarios.fd_inheritance = fail
        contract = {'task_id': 'A11b.2.2.1-containment-f3-recovery-20261007',
                    'base_sha': 'ceae425eacaa8729a9eb8d796e43304918a8c908',
                    'allowed_files': list(runner.EXPECTED_FILES),
                    'required_files': list(runner.EXPECTED_FILES)}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'failure.json'
            (output.parent/'task.json').write_text(json.dumps(contract))
            with patch.object(sys, 'argv', ['run_containment_acceptance.py', '--output', str(output)]), \
                    patch.object(sys, 'platform', 'darwin'), \
                    patch.object(runner, 'CANONICAL', runner.ROOT), \
                    patch.object(runner.Path, 'cwd', return_value=runner.ROOT), \
                    patch.object(kernel, 'host_check'), \
                    patch.object(runner, 'AcceptanceScenarios', return_value=scenarios), \
                    patch.object(runner, 'print', create=True):
                self.assertEqual(runner.main(), 1)
            raw = output.read_bytes()
        data = json.loads(raw)
        self.assertLessEqual(len(raw), wire.LIMIT)
        self.assertEqual(data['scenarios'], {})
        self.assertEqual(set(data['tested_sha256']), {name for name in runner.EXPECTED_FILES
                                                     if not name.startswith('reports/')})
        failure = data['failure']
        self.assertEqual(failure['status'], 'FAIL')
        self.assertEqual(failure['message'], str(primary))
        self.assertEqual(failure['traceback'], primary.primary_before_cleanup['traceback'])
        self.assertEqual(failure['subcase'], 'closed_status')
        self.assertEqual(failure['owned_processes'], [])
        self.assertEqual(failure['fixture_cleanup']['status'], 'RESOLVED')
        self.assertEqual(failure['fixture_cleanup']['original_owned_processes'],
                         [self.probe.inventory()])


class AcceptanceScenarios:
    """Actual process probes, no unittest skips and no mocked native verdicts."""
    def __init__(self):
        self.checks = 0
        self.retained = {}
        self.current_subcase = None
        self.completed_subcases = []
        self.last_fixture_cleanup = None

    def begin_subcase(self, name):
        # Fixed test labels only. Called at actual execution entry, not from a
        # planned list. Completing a probe never marks its whole scenario PASS.
        self.current_subcase = name
        self.last_fixture_cleanup = None

    def finish_subcase(self):
        self.completed_subcases = (self.completed_subcases + [self.current_subcase])[-16:]

    def check(self, value):
        self.checks += 1
        if not value:
            raise AssertionError('containment acceptance assertion failed')
        heartbeat()

    def completed(self, case='exit', timing='ordinary'):
        self.begin_subcase(case)
        probe = Probe(case, timing=timing, hold=(case == 'hung'),
                      timeout=.5 if case == 'hung' else 2)
        with probe_scope(probe):
            row = probe.until('result', seconds=9)
            self.check(row['containment_confirmed'])
            self.check(any(event['event'] == 'handlers_restored' and event['restored']
                           for event in probe.rows))
            self.check(probe.reap() == 0)
            if row['supervisor']:
                self.check(kernel.absent(row['supervisor']['pgid'], group=True))
            if row['primary'] == 'start-failed' and not row['child']:
                raise kernel.NotSupported('isolated trusted supervisor installation unavailable')
        self.last_fixture_cleanup = probe.fixture_cleanup
        return row

    def fd_inheritance(self):
        row = self.completed('descendant')
        evidence = row['evidence']
        self.check(len(evidence['fds']) >= 6)  # Leader and controlled descendant.
        self.check(all(fd['fd'] <= 2 for fd in evidence['fds']))
        self.check(bool(evidence['descendants']))
        unit = IdentityTests('test_host_rejects_python_threads_native_threads_and_custom_reapers')
        unit.debug()
        self.check(True)
        ParentLifecycleTests('test_fd_launch_cleanup_order_and_epipe_preserve_interruption').debug()
        ParentLifecycleTests('test_f1_parent_control_flow_retains_resources_and_primary').debug()
        IdentityTests('test_parent_reparenting_keeps_supervisors_self_anchor').debug()
        self.check(True)
        return []

    def signals(self, number):
        for timing, event in (('ordinary', 'terminal'), ('pre_pipe', 'pre_pipe'),
                              ('spawn', 'spawn'), ('cleanup', 'cleanup')):
            self.begin_subcase(signal.Signals(number).name.lower()+'_'+timing)
            probe = Probe('descendant', hold=(timing == 'ordinary'), timing=timing)
            probe.signal_number = int(number)
            with probe_scope(probe):
                marker = probe.until(event)
                if timing == 'spawn':
                    launched = kernel.identity(marker['supervisor_pid'])
                    self.check(launched.ppid == probe.process.pid)
                    self.check(launched.pid == launched.pgid == launched.sid)
                self.check(kernel.identity(probe.process.pid) == probe.host_identity)
                os.kill(probe.process.pid, number)
                if timing != 'pre_pipe':
                    os.kill(probe.process.pid, number)
                row = probe.until('result', seconds=7)
                self.check(row['primary'] == 'interrupted')
                self.check(row['containment_confirmed'])
                self.check(any(event['event'] == 'handlers_restored' and event['restored']
                               for event in probe.rows))
                self.check(probe.reap() == 0)
                if row['supervisor']:
                    self.check(kernel.absent(row['supervisor']['pgid'], group=True))
            self.finish_subcase()
        return []

    def sigint(self):
        return self.signals(signal.SIGINT)

    def sigterm(self):
        return self.signals(signal.SIGTERM)

    def sighup(self):
        return self.signals(signal.SIGHUP)

    def hung_supervisor(self):
        self.completed_subcases = []
        row = self.completed('hung')
        self.check(row['primary'] == 'supervisor-lost')
        self.check(any(item['type'] == 'TERMINAL' for item in row['diagnostics']['protocol_rows']))
        self.finish_subcase()
        for case in ('closed_status', 'malformed_status', 'lost_before_identity',
                     'registration_failure', 'drain_failure'):
            row = self.completed(case)
            self.check(row['primary'] in ('supervisor-lost', 'protocol-failure'))
            if case == 'closed_status':
                self.check(row['primary'] == 'supervisor-lost')
                self.check(row['diagnostics']['primary_error']['exception_type'] == 'EOFError')
                self.check(not any(item['type'] == 'TERMINAL'
                                   for item in row['diagnostics']['protocol_rows']))
                proof = row['diagnostics']['anchor_verification']
                self.check(proof['mode'] in ('ordinary', 'owned-zombie'))
                if proof['mode'] == 'owned-zombie':
                    self.check(all(item['status'] == 5 for item in proof['kernel_rows']))
                print('closed_status native anchor '+wire.canonical(proof).decode('ascii'), flush=True)
            if case == 'lost_before_identity':
                self.check(not row['child'])
            self.finish_subcase()
        # Missing ACK to a hung supervisor with real SIGTERM.
        probe = Probe('hung', hold=True, timeout=2)
        self.begin_subcase('missing_ACK')
        probe.subcase = 'missing_ACK'
        with probe_scope(probe):
            probe.until('terminal')  # Fixed hung state, real descendant established.
            os.kill(probe.process.pid, signal.SIGTERM)
            row = probe.until('result', seconds=7)
            self.check(row['primary'] == 'interrupted' and row['containment_confirmed'])
            self.check(probe.reap() == 0)
        self.finish_subcase()
        self.check(self.completed('output')['primary'] == 'output-limit')
        self.finish_subcase()
        self.check(self.completed('stderr_output')['primary'] == 'output-limit')
        self.finish_subcase()
        self.check(self.completed('wait')['primary'] == 'timeout')
        self.finish_subcase()
        self.begin_subcase('unresolved')
        self.unresolved_exit()
        self.finish_subcase()
        self.failed_probe_cleanup()
        return []

    def unresolved_exit(self):
        # Real F1 host retention with a fixed synthetic identity fault. The
        # external test controller owns the host, not the supervisor's reap pin.
        # It resolves this fixture by host SIGKILL/EOF, never a guessed PGID.
        probe = Probe('unresolved', timeout=1)
        resolved = False
        row = None
        try:
            row = probe.until('unresolved', seconds=5)
            self.retained = {'unresolved_fixture': row, 'host': probe.host_identity.data()}
            self.check(row['primary'] == 'timeout')
            self.check(row['outcome'] == 'state-unknown' and row['user_action_required'])
            self.check(not row['containment_confirmed'])
            terminal = next(event for event in probe.rows if event['event'] == 'terminal')
            self.check(bool(terminal['evidence']['descendants']))
            for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                self.check(kernel.identity(probe.process.pid) == probe.host_identity)
                os.kill(probe.process.pid, number)
            select.select([], [], [], .05)
            self.check(kernel._info(probe.process.pid, 3, kernel._BSD).status != 5)
            self.check(not any(event['event'] in ('result', 'handlers_restored')
                               for event in probe.rows))
            self.check(kernel.identity(probe.supervisor.pid) == probe.supervisor)
            reported = probe.reported_unresolved_supervisor
            self.check(reported is not None)
            self.check(replace(reported, micros=probe.supervisor.micros) == probe.supervisor)
            self.check(reported.micros == (probe.supervisor.micros+1) % 1000000)
            self.check(not kernel.absent(terminal['child']['pid']))
        finally:
            # This is bounded external resolution of the intentionally unresolved
            # test fixture. It grants no engine retry or ambiguous-ID permission.
            def resolve_fixture():
                nonlocal resolved
                started = time.monotonic()
                probe.cleanup(deadline=started+2)
                self.check(probe.process.returncode == -signal.SIGKILL)
                resolved = probe.fixture_cleanup['status'] == 'RESOLVED'
                self.last_fixture_cleanup = probe.fixture_cleanup
                return probe.fixture_cleanup
            cleanup_preserving_failure(resolve_fixture)
        self.check(resolved)
        for item in [terminal['child']] + terminal['evidence']['descendants']:
            self.check(kernel.absent(item['pid']))
        self.retained = {}

    def failed_probe_cleanup(self):
        # Execute real failure backstops while the controller remains the host's
        # parent. The deliberate assertion is still an assertion, never an
        # engine containment PASS; this subcase validates its cleanup evidence.
        for case in ('unresolved', 'closed_status'):
            self.begin_subcase('fixture_failure_'+case)
            probe = Probe(case, timeout=1)
            primary = AssertionError('containment acceptance assertion failed')
            with patch.object(os, 'kill', wraps=os.kill) as signals, \
                    patch.object(os, 'killpg', wraps=os.killpg) as group_signals:
                try:
                    with probe_scope(probe):
                        row = probe.until('unresolved' if case == 'unresolved' else 'result', seconds=9)
                        if case == 'unresolved':
                            self.check(not row['containment_confirmed'])
                            self.check(not any(event['event'] in ('result', 'handlers_restored')
                                               for event in probe.rows))
                        else:
                            self.check(row['primary'] == 'supervisor-lost')
                            self.check(row['diagnostics']['primary_error']['exception_type'] == 'EOFError')
                        raise primary
                except AssertionError as error:
                    if error is not primary:
                        raise  # An earlier real native failure is not this fixture assertion.
            self.retained = {'controlled_failure': primary.probe_diagnostics,
                             'fixture_cleanup': primary.fixture_cleanup}
            self.last_fixture_cleanup = primary.fixture_cleanup
            cleanup = primary.fixture_cleanup
            self.check(primary.probe_diagnostics['primary_error']['message'] == str(primary))
            self.check(bool(primary.probe_diagnostics['primary_error']['traceback']))
            self.check(primary.probe_diagnostics['subcase'] == case)
            self.check(cleanup['status'] == 'RESOLVED')
            self.check(cleanup['original_owned_processes'][0]['host_identity'] == probe.host_identity.data())
            self.check(cleanup['original_owned_processes'][0]['supervisor'] == probe.supervisor.data())
            self.check(cleanup['evidence']['host_pin_verified'])
            self.check(cleanup['evidence']['decision_before_reap'])
            self.check(cleanup['evidence']['host_reaped'] and cleanup['evidence']['host_absent'])
            self.check(cleanup['evidence']['supervisor_absent'] and cleanup['evidence']['group_absent'])
            self.check(all(item['absent'] for item in cleanup['evidence']['members_absent']))
            self.check(probe not in _FAILED_PROBES)
            self.check(all(call.args == (probe.process.pid, signal.SIGKILL)
                           for call in signals.call_args_list if call.args[1] != 0))
            self.check(not any(call.args[1] != 0 for call in group_signals.call_args_list))
            if case == 'unresolved':
                self.check(probe.process.returncode == -signal.SIGKILL)
            print(self.current_subcase+' '+wire.canonical(cleanup).decode('ascii'), flush=True)
            self.finish_subcase()
            self.retained = {}
        # Native drift rejection uses the actual owned host and real kernel
        # query. Restore its original launch identity only after proving that
        # rejection signaled/reaped nothing, then resolve that same fixture.
        self.begin_subcase('fixture_ambiguous_identity')
        probe = Probe('unresolved', timeout=1)
        with probe_scope(probe):
            probe.until('unresolved')
            original = probe.host_identity
            rejected = None
            try:
                probe.host_identity = replace(original, micros=(original.micros+1) % 1000000)
                with patch.object(os, 'kill', wraps=os.kill) as signals, \
                        patch.object(os, 'killpg', wraps=os.killpg) as group_signals:
                    try:
                        probe.cleanup()
                    except kernel.IdentityUnknown:
                        rejected = probe.fixture_cleanup
                    self.check(rejected is not None and rejected['status'] == 'UNRESOLVED')
                    self.check(not signals.called and not group_signals.called)
                    self.check(probe.process.returncode is None)
                    self.check(probe in _FAILED_PROBES)
            finally:
                probe.host_identity = original
        self.check(probe.fixture_cleanup['status'] == 'RESOLVED')
        self.check(kernel.absent(probe.process.pid))
        self.check(kernel.absent(probe.supervisor.pid) and kernel.absent(probe.supervisor.pgid, group=True))
        self.finish_subcase()

    def leader_exit_descendant(self):
        probe = Probe('leader_exit', hold=True)
        with probe_scope(probe):
            row = probe.until('terminal')
            leader = kernel.Identity.parse(row['child'])
            descendants = row['evidence']['descendants']
            self.check(bool(descendants))
            self.check(kernel._info(leader.pid, 3, kernel._BSD).status == 5)  # SZOMB
            for data in descendants:
                item = kernel.Identity.parse(data)
                observed = kernel.identity(item.pid)
                # Leader exit reparents descendants. PPID is ownership-critical
                # for the supervisor anchor, but not stable member identity.
                self.check(replace(observed, ppid=item.ppid) == item)
                self.check(kernel._info(item.pid, 3, kernel._BSD).status != 5)
                self.check(item.pgid == row['supervisor']['pgid'])
            os.kill(probe.process.pid, signal.SIGTERM)
            final = probe.until('result', seconds=7)
            self.check(final['containment_confirmed'])
            self.check(all(kernel.absent(item['pid']) for item in descendants))
            self.check(probe.reap() == 0)
        return []

    def identity_reuse(self):
        suite = unittest.TestSuite(IdentityTests(name) for name in
            ('test_anchor_decision_precedes_reap_and_no_signal_after_reap',
             'test_mismatch_reuse_and_released_ownership_never_signal',
             'test_verification_to_signal_exit_keeps_owned_pin',
             'test_permission_is_not_disappearance'))
        result = unittest.TestResult()
        suite.run(result)
        self.check(result.testsRun == 4 and result.wasSuccessful() and not result.skipped)
        # The actual parent (not this grandparent) injects supervisor SIGKILL
        # after its fresh verification and BEFORE group signaling. It confirms
        # an unreaped zombie via kqueue/kernel status, then signals the pinned
        # group and only afterward reaps. No outer snapshot-based PID signal.
        row = self.completed('anchor_race')
        self.check(row['primary'] == 'supervisor-lost')
        self.check(bool(row['evidence']['descendants']))
        return []

    def macos_start_time(self):
        own = kernel.identity(os.getpid())
        self.check(own.uid == os.geteuid() and own.pid == os.getpid())
        self.check(own.seconds > 0 and 0 <= own.micros < 1000000)
        self.check(kernel.identity(own.pid) == own)
        self.check(kernel._info(own.pid, 4, kernel._Task).narrow9 == 1)
        self.check(kernel.Identity.parse(own.data()) == own)
        return []

    def kqueue_non_reaping(self):
        for zombie_first in (False, True):
            process = subprocess.Popen([sys.executable, '-I', '-c',
                'import time; time.sleep(.1)'], shell=False, close_fds=True,
                start_new_session=True, env=wire.fresh_environment(),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            bound = anchor = note = None
            try:
                bound = kernel.identity(process.pid)
                anchor = kernel.Anchor(process, bound)
                if zombie_first:
                    until = time.monotonic()+1
                    while kernel._info(process.pid, 3, kernel._BSD).status != 5:
                        self.check(time.monotonic() < until)
                        select.select([], [], [], .005)
                    # Establish the same owned, unreaped zombie BEFORE trying
                    # a new registration; ESRCH alone cannot prove an exit.
                    self.check(anchor.verify(deadline=until) == bound)
                    self.check(anchor.verification['mode'] in ('ordinary', 'owned-zombie'))
                    self.check(process.returncode is None and not anchor.reaped)
                    self.check(kernel._info(process.pid, 3, kernel._BSD).status == 5)
                else:
                    self.check(kernel._info(process.pid, 3, kernel._BSD).status != 5)
                registration = 'NOTE_EXIT'
                try:
                    note = kernel.ExitNote(process.pid)
                except ProcessLookupError as error:
                    if not zombie_first or error.errno != errno.ESRCH:
                        raise
                    # Darwin may refuse EVFILT_PROC for an already-exited
                    # child. Require fresh zombie/identity/pin proof below,
                    # rather than interpreting the registration error as an
                    # exit event. The before-exit path still requires NOTE_EXIT.
                    registration = 'ESRCH_AFTER_OWNED_ZOMBIE'
                until = time.monotonic()+1
                if note is not None:
                    while not note.exited():
                        self.check(time.monotonic() < until)
                        select.select([], [], [], .005)
                else:
                    self.check(kernel._info(process.pid, 3, kernel._BSD).status == 5)
                self.check(process.returncode is None)
                while kernel._info(process.pid, 3, kernel._BSD).status != 5:
                    self.check(time.monotonic() < until)
                    select.select([], [], [], .005)
                self.check(kernel._info(process.pid, 3, kernel._BSD).status == 5)
                # A general SID query may reject a zombie on Darwin. The owned
                # anchor must prove continuity, without mocking that capability.
                self.check(anchor.verify(deadline=until) == bound)
                self.check(anchor.verification['mode'] in ('ordinary', 'owned-zombie'))
                self.check(process.returncode is None and not anchor.reaped)
                self.retained = {'zombie_first': zombie_first, 'registration': registration,
                                 'anchor': bound.data(),
                                 'verification': anchor.verification, 'unreaped': True}
                print('kqueue_non_reaping native anchor '+wire.canonical(self.retained).decode('ascii'),
                      flush=True)
                anchor.kill_group(deadline=until)
                anchor.reap(time.monotonic()+1)
                self.check(kernel.absent(bound.pgid, group=True))
            finally:
                try:
                    cleanup_preserving_failure(lambda: cleanup_fixture(process, bound))
                finally:
                    if note is not None:
                        cleanup_preserving_failure(note.close)
            self.retained = {}
        IdentityTests('test_registration_failure_closes_notification_fd').debug()
        self.check(True)
        return []

    def protocol_framing(self):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProtocolTests)
        result = unittest.TestResult()
        suite.run(result)
        self.check(result.testsRun > 0 and result.wasSuccessful() and not result.skipped)
        # Real invalid launch with inherited endpoints and a wrong START frame.
        r, w = os.pipe()
        sr, sw = os.pipe()
        process = None
        bound = None
        try:
            with tempfile.TemporaryDirectory() as neutral:
                process = subprocess.Popen([sys.executable, '-I', '-m', 'llmtool._git_supervisor'],
                    shell=False, start_new_session=True, close_fds=True, pass_fds=(r, sw),
                    cwd=neutral, env=wire.fresh_environment(), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                os.close(r)
                r = None
                os.close(sw)
                sw = None
                bound = kernel.identity(process.pid)
                os.write(w, b'\0'*4)
                note = kernel.ExitNote(process.pid)
                try:
                    until = time.monotonic()+2
                    while not note.exited():
                        self.check(time.monotonic() < until)
                        select.select([], [], [], .005)
                    owner = kernel.Anchor(process, bound)
                    cleanup_deadline = time.monotonic()+1
                    owner.kill_group(deadline=cleanup_deadline)
                    owner.reap(cleanup_deadline)
                    self.check(process.returncode != 0)
                finally:
                    cleanup_preserving_failure(note.close)
        finally:
            try:
                if process is not None:
                    cleanup_preserving_failure(lambda: cleanup_fixture(process, bound))
            finally:
                for fd in (r, w, sr, sw):
                    if fd is not None:
                        cleanup_preserving_failure(lambda fd=fd: os.close(fd))
        return []

    def code_runtime_binding(self):
        BindingBoundaryTests('test_complete_pinned_manifest_and_drift_rejection').debug()
        BindingBoundaryTests('test_environment_is_fresh').debug()
        BindingBoundaryTests('test_supervisor_bad_code_handshake_rejects_before_ready_and_spawn').debug()
        BindingBoundaryTests('test_second_binding_check_rejects_drift_before_child_spawn').debug()
        self.check(True)
        # Actual isolated launch resolves and independently validates current
        # caller-pinned manifest before it can deliver READY or spawn a child.
        row = self.completed('exit')
        self.check(row['primary'] == 'complete')
        self.check(bool(row['child']))
        return []

    def public_import_boundary(self):
        self.check(boundary_check() > 0)
        BindingBoundaryTests('test_direct_import_and_runpy_cannot_spawn').debug()
        BindingBoundaryTests('test_arbitrary_host_rejected_before_spawn').debug()
        BindingBoundaryTests('test_fake_child_direct_wrapper_missing_handshake_cannot_spawn').debug()
        self.check(True)
        for module in ('llmtool._git_supervisor', 'llmtool._git_containment_child',
                       'llmtool._git_containment'):
            with tempfile.TemporaryDirectory() as neutral:
                # Wrapper/direct -m have no internal endpoint/launch handshake.
                for command in ([sys.executable, '-I', '-m', module],
                        [sys.executable, '-I', '-c',
                         'import runpy; runpy.run_module('+repr(module)+', run_name="__main__")']):
                    process = subprocess.Popen(command, cwd=neutral, shell=False,
                        close_fds=True, start_new_session=True, env=wire.fresh_environment(),
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    bound = note = None
                    try:
                        bound = kernel.identity(process.pid)
                        note = kernel.ExitNote(process.pid)
                        until = time.monotonic()+2
                        while not note.exited():
                            self.check(time.monotonic() < until)
                            select.select([], [], [], .005)
                        owner = kernel.Anchor(process, bound)
                        cleanup_deadline = time.monotonic()+1
                        owner.kill_group(deadline=cleanup_deadline)
                        owner.reap(cleanup_deadline)
                        self.check(process.returncode != 0)
                    finally:
                        try:
                            cleanup_preserving_failure(lambda: cleanup_fixture(process, bound))
                        finally:
                            if note is not None:
                                cleanup_preserving_failure(note.close)
        return []

    def parent_sigkill(self, popen):
        repetitions = []
        for _ in range(20):
            probe = Probe('descendant', hold=True, popen=popen)
            with probe_scope(probe):
                row = probe.until('terminal')
                self.retained = {'completed_repetitions': repetitions.copy(),
                                 'active_identity_evidence': row}
                supervisor = kernel.Identity.parse(row['supervisor'])
                leader = kernel.Identity.parse(row['child'])
                descendants = [kernel.Identity.parse(v) for v in row['evidence']['descendants']]
                self.check(bool(descendants))
                self.check(kernel.identity(supervisor.pid) == supervisor)
                self.check(kernel.identity(leader.pid) == leader)
                self.check(supervisor.pid == supervisor.pgid == supervisor.sid)
                self.check(leader.pgid == supervisor.pgid)
                for item in descendants:
                    self.check(kernel.identity(item.pid) == item)
                    self.check(item.pgid == supervisor.pgid and item.sid == supervisor.sid)
                    self.check(kernel._info(item.pid, 3, kernel._BSD).status != 5)
                if popen:
                    stresses = [event for event in probe.rows if event['event'] == 'stress']
                    self.check(len(stresses) == 1)
                    stress = kernel.Identity.parse(stresses[0]['identity'])
                    self.check(kernel.identity(stress.pid) == stress)
                    self.check(kernel._info(stress.pid, 3, kernel._BSD).status != 5)
                self.check(kernel.identity(probe.process.pid) == probe.host_identity)
                started = time.monotonic()
                os.kill(probe.process.pid, signal.SIGKILL)  # Real host parent only.
                until = started+2
                while True:
                    group_absent = leader_absent = descendants_absent = False
                    try:
                        group_absent = kernel.absent(supervisor.pgid, group=True)
                        leader_absent = kernel.absent(leader.pid)
                        descendants_absent = all(kernel.absent(item.pid) for item in descendants)
                    except PermissionError:
                        pass  # EPERM is inconclusive, never absence or PASS.
                    if group_absent and leader_absent and descendants_absent:
                        break
                    self.check(time.monotonic() < until)
                    # Zombies remain present until launchd reaps them; they are
                    # never counted as absent/executing merely from kill(0).
                    select.select([], [], [], .005)
                latency = time.monotonic()-started
                self.check(0 <= latency <= 2)
                self.check(kernel.absent(supervisor.pid))
                if popen:
                    # This unrelated finite child never holds a control FD.
                    # Require eventual disappearance as well, without signaling
                    # an orphan by a remembered PID. Its own natural exit is fixed.
                    while not kernel.absent(stress.pid):
                        self.check(time.monotonic() < started+5)
                        select.select([], [], [], .005)
                self.check(probe.reap() == -signal.SIGKILL)
                repetitions.append({'leader_pid': leader.pid, 'pgid': supervisor.pgid,
                    'descendant_pids': [item.pid for item in descendants],
                    'latency_seconds': latency, 'identity_verified': True,
                    'leader_absent': leader_absent, 'descendants_absent': descendants_absent,
                    'group_absent': group_absent, 'containment_confirmed': True})
                self.retained = {'completed_repetitions': repetitions.copy()}
        return repetitions

    def parent_sigkill_plain(self):
        return self.parent_sigkill(False)

    def parent_sigkill_popen(self):
        return self.parent_sigkill(True)


if __name__ == '__main__':
    unittest.main()
