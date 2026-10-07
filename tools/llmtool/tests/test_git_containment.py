"""Deterministic unit coverage plus explicitly invoked actual-Mac scenarios.

unittest discovery runs the unit classes. The outer runner separately invokes
AcceptanceScenarios via run_containment_acceptance.py; mocks never satisfy its
native signal, process, kqueue or SIGKILL repetitions.
"""
import ast
from dataclasses import replace
import errno
import importlib
import os
from pathlib import Path
import runpy
import select
import signal
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
    return [{'pid': probe.process.pid,
             'host_identity': probe.host_identity.data() if probe.host_identity else {},
             'supervisor': probe.supervisor.data() if probe.supervisor else {}}
            for probe in _FAILED_PROBES]


def cleanup_fixture(process, bound):
    """Assertion backstop for direct unit/probe-owned session leaders."""
    if process.returncode is not None:
        return  # Already reaped; no numeric signal is permitted.
    try:
        if bound is None:
            raise kernel.IdentityUnknown('fixture launch identity unavailable')
        owner = kernel.Anchor(process, bound)
        deadline = time.monotonic()+2
        owner.kill_group()
        owner.reap(deadline)
        while not kernel.absent(bound.pgid, group=True):
            heartbeat()
            if time.monotonic() >= deadline:
                raise kernel.IdentityUnknown('fixture group disappearance unconfirmed')
            select.select([], [], [], .005)
    except BaseException:
        _FAILED_PROBES.append(SimpleNamespace(process=process, host_identity=bound,
                                            supervisor=None))
        raise AssertionError('fixture cleanup unresolved; external action required') from None


def pinned():
    return {name: wire.file_hash(PACKAGE / name) for name in wire.ARTIFACTS}


def boundary_check():
    private = {'_git_containment.py', '_git_supervisor.py',
               '_git_containment_protocol.py', '_git_process_identity.py',
               '_git_containment_child.py'}
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


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.bound = kernel.Identity(123, 123, 123, os.geteuid(), os.getpid(), 1, 2)
        self.process = Mock(pid=123, returncode=None)

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
                patch.object(kernel, '_info', return_value=SimpleNamespace(status=5)), \
                patch.object(kernel.os, 'kill', side_effect=lambda *args: calls.append('leader')), \
                patch.object(kernel.os, 'killpg', side_effect=lambda *args: calls.append('group')):
            anchor.kill_group(exit_after_verify=True, deadline=time.monotonic()+1)
            self.process.wait.assert_not_called()
            anchor.reap(time.monotonic()+1)
        self.assertEqual(calls, ['verify', 'leader', 'group', 'reap'])
        note.close.assert_called_once()

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

    def test_parent_reparenting_keeps_supervisors_self_anchor(self):
        module = importlib.import_module('llmtool._git_supervisor')
        class LastAct(BaseException):
            pass
        with patch.object(module.os, 'getpid', return_value=self.bound.pid), \
                patch.object(module, 'identity', return_value=replace(self.bound, ppid=1)), \
                patch.object(module.os, 'killpg', side_effect=LastAct) as kill, \
                patch.object(module.os, '_exit') as exit_process:
            with self.assertRaises(LastAct):
                module._last_act(self.bound)
            kill.assert_called_once_with(self.bound.pgid, signal.SIGKILL)
            exit_process.assert_not_called()
        with patch.object(module.os, 'getpid', return_value=self.bound.pid), \
                patch.object(module, 'identity', return_value=replace(self.bound, micros=3)), \
                patch.object(module.os, 'killpg') as kill, \
                patch.object(module.os, '_exit', side_effect=LastAct):
            with self.assertRaises(LastAct):
                module._last_act(self.bound)
            kill.assert_not_called()


class BindingBoundaryTests(unittest.TestCase):
    def test_complete_pinned_manifest_and_drift_rejection(self):
        manifest = pinned()
        binding = wire.runtime_binding(manifest)
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


class ParentLifecycleTests(unittest.TestCase):
    def exercise(self, unresolved, terminal_failure=None, close_fault=False):
        parent = kernel.Identity(os.getpid(), os.getpid(), os.getpid(), os.geteuid(), 1, 1, 2)
        bound = kernel.Identity(123, 123, 123, os.geteuid(), os.getpid(), 1, 2)
        process = Mock(pid=123, returncode=None)
        monitor = Mock()
        monitor.exited.return_value = False
        if close_fault:
            monitor.close.side_effect = OSError('synthetic undisclosed exception text')
        anchor = Mock(bound=bound, signal_faults=[])
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
                raise BrokenPipeError()
        signals.install.side_effect = lambda: calls.append('install')
        def restore():
            for fd in fds:
                with self.assertRaises(OSError):
                    os.fstat(fd)
            self.assertEqual(calls[-1], 'gone')
            calls.append('restore')
        signals.restore.side_effect = restore
        def kill_group():
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
        self.host_identity = None
        try:
            self.host_identity = kernel.identity(self.process.pid)
        except BaseException:
            _FAILED_PROBES.append(self)  # Never release an ambiguous launch pin.
            raise
        os.set_blocking(self.process.stdout.fileno(), False)

    def next(self, deadline, allow_unresolved=False):
        while time.monotonic() < deadline:
            heartbeat()
            if b'\n' in self.buffer:
                raw, _, rest = self.buffer.partition(b'\n')
                self.buffer = bytearray(rest)
                row = wire.strict_load(bytes(raw))
                self.rows.append(row)
                if row.get('event') == 'ready':
                    self.supervisor = kernel.Identity.parse(row['supervisor'])
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
        deadline = time.monotonic()+seconds
        while True:
            row = self.next(deadline, allow_unresolved=(kind == 'unresolved'))
            if row['event'] == kind:
                return row

    def reap(self):
        result = self.process.wait(timeout=6)
        self.process.stdout.close()
        return result

    def cleanup(self):
        # The outer probe owns only the host's reap pin. Ask that host to use
        # its supervisor anchor; a snapshot of a grandchild never grants this
        # process authority to signal the grandchild's numeric group.
        if self.process.returncode is not None:
            self.process.stdout.close()
            return
        note = None
        try:
            if kernel.identity(self.process.pid) != self.host_identity:
                raise kernel.IdentityUnknown('probe host ownership unknown')
            note = kernel.ExitNote(self.process.pid)
            os.kill(self.process.pid, signal.SIGTERM)  # Owned, unreaped host only.
            deadline = time.monotonic()+6
            while not note.exited():
                heartbeat()
                if time.monotonic() >= deadline:
                    raise kernel.IdentityUnknown('probe host retained unresolved ownership')
                select.select([], [], [], .005)
            if self.supervisor is not None and not kernel.absent(self.supervisor.pgid, group=True):
                raise kernel.IdentityUnknown('probe group disappearance unconfirmed')
            owner = kernel.Anchor(self.process, self.host_identity)
            owner.kill_group()
            owner.reap(deadline)
            self.process.stdout.close()
        except BaseException:
            _FAILED_PROBES.append(self)
            raise AssertionError('probe cleanup unresolved; external action required') from None
        finally:
            if note is not None:
                note.close()


class AcceptanceScenarios:
    """Actual process probes, no unittest skips and no mocked native verdicts."""
    def __init__(self):
        self.checks = 0
        self.retained = {}

    def check(self, value):
        self.checks += 1
        if not value:
            raise AssertionError('containment acceptance assertion failed')
        heartbeat()

    def completed(self, case='exit', timing='ordinary'):
        probe = Probe(case, timing=timing, hold=(case == 'hung'),
                      timeout=.5 if case == 'hung' else 2)
        try:
            row = probe.until('result', seconds=9)
            self.check(row['containment_confirmed'])
            self.check(any(event['event'] == 'handlers_restored' and event['restored']
                           for event in probe.rows))
            self.check(probe.reap() == 0)
            if row['supervisor']:
                self.check(kernel.absent(row['supervisor']['pgid'], group=True))
            if row['primary'] == 'start-failed' and not row['child']:
                raise kernel.NotSupported('isolated trusted supervisor installation unavailable')
            return row
        finally:
            probe.cleanup()

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
            probe = Probe('descendant', hold=(timing == 'ordinary'), timing=timing)
            try:
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
            finally:
                probe.cleanup()
        return []

    def sigint(self):
        return self.signals(signal.SIGINT)

    def sigterm(self):
        return self.signals(signal.SIGTERM)

    def sighup(self):
        return self.signals(signal.SIGHUP)

    def hung_supervisor(self):
        row = self.completed('hung')
        self.check(row['primary'] == 'supervisor-lost')
        for case in ('closed_status', 'malformed_status', 'lost_before_identity',
                     'registration_failure', 'drain_failure'):
            row = self.completed(case)
            self.check(row['primary'] in ('supervisor-lost', 'protocol-failure'))
            if case == 'lost_before_identity':
                self.check(not row['child'])
        # Missing ACK to a hung supervisor with real SIGTERM.
        probe = Probe('hung', hold=True, timeout=2)
        try:
            probe.until('terminal')  # Fixed hung state, real descendant established.
            os.kill(probe.process.pid, signal.SIGTERM)
            row = probe.until('result', seconds=7)
            self.check(row['primary'] == 'interrupted' and row['containment_confirmed'])
            self.check(probe.reap() == 0)
        finally:
            probe.cleanup()
        self.check(self.completed('output')['primary'] == 'output-limit')
        self.check(self.completed('stderr_output')['primary'] == 'output-limit')
        self.check(self.completed('wait')['primary'] == 'timeout')
        self.unresolved_exit()
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
            self.check(not kernel.absent(terminal['child']['pid']))
        finally:
            # This is bounded external resolution of the intentionally unresolved
            # test fixture. It grants no engine retry or ambiguous-ID permission.
            owner = kernel.Anchor(probe.process, probe.host_identity)
            started = time.monotonic()
            owner.kill_group()
            owner.reap(started+2)
            self.check(probe.process.returncode == -signal.SIGKILL)
            if probe.supervisor is not None:
                while not kernel.absent(probe.supervisor.pgid, group=True):
                    self.check(time.monotonic() < started+2)
                    select.select([], [], [], .005)
                resolved = True
            probe.process.stdout.close()
        self.check(resolved)
        for item in [terminal['child']] + terminal['evidence']['descendants']:
            self.check(kernel.absent(item['pid']))
        self.retained = {}

    def leader_exit_descendant(self):
        probe = Probe('leader_exit', hold=True)
        try:
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
        finally:
            probe.cleanup()
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
                else:
                    self.check(kernel._info(process.pid, 3, kernel._BSD).status != 5)
                note = kernel.ExitNote(process.pid)
                until = time.monotonic()+1
                while not note.exited():
                    self.check(time.monotonic() < until)
                    select.select([], [], [], .005)
                self.check(process.returncode is None)
                self.check(kernel.identity(process.pid) == bound)
                anchor.kill_group()
                anchor.reap(time.monotonic()+1)
                self.check(kernel.absent(bound.pgid, group=True))
            finally:
                try:
                    cleanup_fixture(process, bound)
                finally:
                    if note is not None:
                        note.close()
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
                    owner.kill_group()
                    owner.reap(time.monotonic()+1)
                    self.check(process.returncode != 0)
                finally:
                    note.close()
        finally:
            try:
                if process is not None:
                    cleanup_fixture(process, bound)
            finally:
                for fd in (r, w, sr, sw):
                    if fd is not None:
                        os.close(fd)
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
                        owner.kill_group()
                        owner.reap(time.monotonic()+1)
                        self.check(process.returncode != 0)
                    finally:
                        try:
                            cleanup_fixture(process, bound)
                        finally:
                            if note is not None:
                                note.close()
        return []

    def parent_sigkill(self, popen):
        repetitions = []
        for _ in range(20):
            probe = Probe('descendant', hold=True, popen=popen)
            parent_killed = False
            try:
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
                parent_killed = True
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
            finally:
                if not parent_killed:
                    probe.cleanup()
                elif probe.process.returncode is None:
                    # Host SIGKILL was already sent. Only reap that owned host;
                    # never signal remembered released supervisor/group IDs.
                    probe.reap()
        return repetitions

    def parent_sigkill_plain(self):
        return self.parent_sigkill(False)

    def parent_sigkill_popen(self):
        return self.parent_sigkill(True)


if __name__ == '__main__':
    unittest.main()
