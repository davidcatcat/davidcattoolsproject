"""Private synchronous fake-child containment harness; no product caller.

The reviewed host is tests/containment_probe_parent.py, a closed main-thread
program. Counts alone are insufficient: this host has no async plugins/native
fork callbacks or foreign reaper. Arbitrary embedding hosts are rejected.
No Action, Approval, repository path or executable argument is accepted.
"""
from dataclasses import dataclass, field, replace
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time

from . import _git_containment_protocol as wire
from ._git_process_identity import (Anchor, ExitNote, Identity, IdentityUnknown,
                                   NotSupported, absent, host_check, identity)

_UNRESOLVED = []  # Retain ownership: Popen.__del__ must not release an anchor.
_HOST = Path(__file__).resolve().parents[1] / 'tests/containment_probe_parent.py'
_PRIMARY_FAILURES = frozenset(('timeout', 'output-limit', 'protocol-failure',
                               'supervisor-lost', 'start-failed'))


@dataclass(frozen=True)
class _Host:
    manifest: dict
    host_sha256: str


@dataclass(frozen=True)
class Outcome:
    primary: str
    containment_confirmed: bool
    supervisor: dict
    child: dict
    evidence: dict
    cleanup_faults: tuple = ()
    diagnostics: dict = field(default_factory=dict)


class ContainmentUnresolved(BaseException):
    """F1: transfer retained responsibility to the fixed operator-wait host.

    No ordinary result or handler restoration. Holds the process, pipes,
    monitor and original primary failure. The host emits bounded evidence and
    waits for external resolution; it never signals an uncertain numeric ID.
    """
    def __init__(self, primary, ownership):
        super().__init__('containment/state unknown; user action required')
        self.primary, self.ownership = primary, ownership
        self.primary_exception = ownership.get('primary_exception')
        self.diagnostics = ownership.get('diagnostics', {})
        _UNRESOLVED.append(self)


def _event(kind, **data):
    # Harness-only synthetic metadata; no provider output, environment or paths.
    os.write(1, wire.canonical({'event': kind, **data}) + b'\n')


class _Signals:
    def __init__(self):
        self.first = None
        self.first_time = None
        self.previous = {}

    def record(self, number, frame):
        if self.first is None:
            self.first = number
            self.first_time = time.monotonic()

    def install(self):
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            self.previous[number] = signal.getsignal(number)
            signal.signal(number, self.record)

    def restore(self):
        for number, previous in self.previous.items():
            signal.signal(number, previous)


def _validate_host(host):
    if (type(host) is not _Host or Path(sys.argv[0]).resolve() != _HOST
            or not sys.flags.isolated
            or Path(getattr(sys.modules['__main__'], '__file__', '')).resolve() != _HOST
            or sys.gettrace() is not None or sys.getprofile() is not None
            or wire.file_hash(_HOST) != host.host_sha256):
        raise NotSupported('reviewed standalone harness host required')
    host_check()


def _group_gone(anchor, child, evidence, deadline):
    # Passive observations only after reap. Never signal released remembered IDs.
    pids = [child['pid']] if child else []
    pids.extend(row['pid'] for row in evidence.get('descendants', []))
    while time.monotonic() < deadline:
        try:
            if absent(anchor.bound.pgid, group=True) and all(absent(pid) for pid in pids):
                return True
        except PermissionError:
            pass  # Inconclusive; passive bounded verification, never absence.
        select.select([], [], [], .005)
    return False


def _run(host, case='exit', timeout=2, *, hold_terminal=False,
         inheritance_stress=False, timing='ordinary'):
    """Fixed engineering cases; only the sealed standalone host may enter.

    inheritance_stress is explicitly harness-only. It is the sole additional
    Popen during the window and never becomes a production permission. The
    other modes insert fixed signal timing points, not spawning callbacks.
    """
    _validate_host(host)
    if (case not in wire.CASES or timing not in ('ordinary', 'pre_pipe', 'spawn', 'cleanup')
            or type(timeout) not in (int, float) or not 0 < timeout <= 30
            or type(hold_terminal) is not bool or type(inheritance_stress) is not bool):
        wire.reject()
    binding = wire.runtime_binding(host.manifest, case, timeout)
    digest = wire.verify_binding(binding)
    parent = identity(os.getpid())
    nonce = os.urandom(32).hex()
    neutral = tempfile.TemporaryDirectory(prefix='llmtool-fake-containment-', dir='/private/tmp')
    signals = _Signals()
    owned_fds = []
    process = anchor = note = stress = None
    primary, child, evidence = None, {}, {}  # No failure observed yet.
    confirmed = False
    cleanup_faults = []
    shutdown_deadline = None
    ready = False
    primary_exception = None
    diagnostics = {'timing': timing, 'signal': None, 'expected_event': 'launch',
                   'primary_error': None, 'protocol_rows': [], 'cleanup_errors': [],
                   'cleanup_outcome': 'not-started', 'anchor_verification': None}
    try:
        signals.install()  # Before pipes, including the injected pre-pipe signal.
        if timing == 'pre_pipe':
            _event('pre_pipe')
            until = time.monotonic()+.2
            while signals.first is None and time.monotonic() < until:
                select.select([], [], [], .005)
        if signals.first is not None:
            primary, confirmed = 'interrupted', True
            diagnostics['cleanup_outcome'] = 'no-process'
            return Outcome(primary, True, {}, {}, {}, diagnostics=diagnostics)
        control_r, control_w = os.pipe()
        owned_fds.extend((control_r, control_w))
        status_r, status_w = os.pipe()
        owned_fds.extend((status_r, status_w))
        for fd in owned_fds:
            os.set_inheritable(fd, False)
            if os.get_inheritable(fd):
                wire.reject()
        pipes = [[os.fstat(fd).st_dev, os.fstat(fd).st_ino]
                 for fd in (control_r, status_w)]
        handshake_deadline = time.monotonic()+2
        process = subprocess.Popen([sys.executable, '-I', '-m',
                  'llmtool._git_supervisor'], shell=False, start_new_session=True,
                  close_fds=True, pass_fds=(control_r, status_w),
                  cwd=neutral.name, env=wire.fresh_environment(),
                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL)
        # Immediately close every parent copy of supervisor-only endpoints.
        for fd in (control_r, status_w):
            os.close(fd)
            owned_fds.remove(fd)
        if timing == 'spawn':
            # Real launch-phase signal: the supervisor now exists and waits for
            # START. This fixed harness timing point is not a spawn callback.
            _event('spawn', supervisor_pid=process.pid)
            until = min(handshake_deadline, time.monotonic()+.2)
            while signals.first is None and time.monotonic() < until:
                select.select([], [], [], .005)
        bound = identity(process.pid)
        anchor = Anchor(process, bound)
        note = ExitNote(process.pid)
        reader = wire.Reader(status_r)
        diagnostics['expected_event'] = 'READY'
        wire.send(control_w, wire.message('START', binding=binding,
                  parent=parent.data(), launch=bound.data(), nonce=nonce,
                  case=case, timeout=timeout, pipes=pipes), handshake_deadline)
        deadline = handshake_deadline+timeout
        cancel_deadline = None
        ready = delivered = ack = terminal = False
        if inheritance_stress:
            # Synthetic stress only, close_fds prevents retaining our writer.
            stress = subprocess.Popen([sys.executable, '-I', '-c',
                'import time; time.sleep(1)'], shell=False, close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=wire.fresh_environment(), cwd=neutral.name)
            _event('stress', identity=identity(stress.pid).data())
        while True:
            now = time.monotonic()
            if signals.first is not None and cancel_deadline is None:
                if primary not in _PRIMARY_FAILURES:
                    primary = 'interrupted'
                shutdown_deadline = signals.first_time+5
                cancel_deadline = min(now+1, shutdown_deadline)
                deadline = min(deadline, shutdown_deadline)
                diagnostics['expected_event'] = 'CANCEL_ACK'
                wire.send(control_w, wire.message('CANCEL', nonce=nonce), cancel_deadline)
            if (not ready and now >= handshake_deadline):
                if primary not in _PRIMARY_FAILURES and primary != 'interrupted':
                    primary = 'start-failed'
                break
            if cancel_deadline is not None and not ack and now >= cancel_deadline:
                break  # Missing ACK escalates; retain interruption primary.
            if now >= deadline:
                if primary not in _PRIMARY_FAILURES and primary != 'interrupted':
                    primary = 'supervisor-lost' if ready else 'start-failed'
                break
            value = reader.read()
            if value is not None:
                # Closed protocol metadata only, never nonce/binding/argv/output.
                diagnostics['protocol_rows'].append({'type': value['type']})
                diagnostics['protocol_rows'] = diagnostics['protocol_rows'][-4:]
                if value['nonce'] != nonce:
                    wire.reject()
                kind = value['type']
                if kind == 'READY' and not ready and not delivered:
                    if value['digest'] != digest or Identity.parse(value['supervisor']) != bound:
                        wire.reject()
                    ready = True
                    diagnostics['expected_event'] = 'CHILD_IDENTITY'
                    _event('ready', supervisor=bound.data())
                elif kind == 'CHILD_IDENTITY' and ready and not delivered:
                    item = Identity.parse(value['child'])
                    if item.ppid != bound.pid or item.pgid != bound.pgid or item.sid != bound.sid:
                        wire.reject()
                    child, delivered = item.data(), True
                    diagnostics['expected_event'] = 'TERMINAL'
                    _event('child', child=child)
                elif kind == 'CANCEL_ACK' and cancel_deadline is not None and not ack:
                    ack = True
                elif kind == 'TERMINAL' and ready and not terminal:
                    if not delivered:
                        wire.reject()
                    evidence = value['evidence']
                    if (type(evidence) is not dict or (evidence and
                            (set(evidence) != {'child', 'descendants', 'fds'}
                             or evidence['child'] != child))):
                        wire.reject()
                    terminal = True
                    if primary != 'interrupted':
                        primary = value['outcome']
                    _event('terminal', supervisor=bound.data(), child=child,
                           evidence=evidence, primary=primary)
                    if not hold_terminal:
                        break
                else:
                    wire.reject()
            if note.exited():
                if primary not in _PRIMARY_FAILURES and primary != 'interrupted':
                    primary = 'supervisor-lost' if ready else 'start-failed'
                break
            select.select([status_r], [], [], .005)
    except (EOFError, BrokenPipeError, TimeoutError) as error:
        primary_exception = error
        diagnostics['primary_error'] = wire.exception_evidence(error)
        if signals.first is not None and primary not in _PRIMARY_FAILURES:
            primary = 'interrupted'
        if primary not in _PRIMARY_FAILURES and primary != 'interrupted':
            primary = 'supervisor-lost' if ready else 'start-failed'
    except wire.ProtocolFailure as error:
        primary_exception = error
        diagnostics['primary_error'] = wire.exception_evidence(error)
        if signals.first is not None and primary not in _PRIMARY_FAILURES:
            primary = 'interrupted'
        if primary not in _PRIMARY_FAILURES and primary != 'interrupted':
            primary = 'protocol-failure'
    except (OSError, IdentityUnknown, NotSupported) as error:
        primary_exception = error
        diagnostics['primary_error'] = wire.exception_evidence(error)
        if signals.first is not None and primary not in _PRIMARY_FAILURES:
            primary = 'interrupted'
        if primary not in _PRIMARY_FAILURES and primary != 'interrupted':
            primary = 'start-failed' if anchor is None else 'supervisor-lost'
    except Exception as error:
        primary_exception = error
        diagnostics['primary_error'] = wire.exception_evidence(error)
        if signals.first is not None and primary not in _PRIMARY_FAILURES:
            primary = 'interrupted'
        if primary not in _PRIMARY_FAILURES and primary != 'interrupted':
            primary = 'supervisor-lost' if process is not None else 'start-failed'
    except BaseException as error:
        primary_exception = error
        diagnostics['primary_error'] = wire.exception_evidence(error)
        if signals.first is not None and primary not in _PRIMARY_FAILURES:
            primary = 'interrupted'
        if primary not in _PRIMARY_FAILURES and primary != 'interrupted':
            primary = 'supervisor-lost' if process is not None else 'start-failed'
        raise
    finally:
        if signals.first is not None:
            if primary not in _PRIMARY_FAILURES:
                primary = 'interrupted'
            if shutdown_deadline is None:
                shutdown_deadline = signals.first_time+5
        if process is not None:
            cleanup_deadline = shutdown_deadline or time.monotonic()+5
            diagnostics['cleanup_outcome'] = 'pending'
            diagnostics['failure_expected_event'] = diagnostics['expected_event']
            try:
                if anchor is None:
                    raise IdentityUnknown('missing supervisor anchor')
                if timing == 'cleanup':
                    _event('cleanup')
                    until = min(cleanup_deadline, time.monotonic()+.2)
                    while time.monotonic() < until:
                        select.select([], [], [], .005)
                if case == 'unresolved':
                    # Fixed fake-harness F1 fault: simulate ambiguous start time.
                    # The live synthetic group stays owned; no numeric signal
                    # is permitted from this deliberately mismatched anchor.
                    anchor.bound = replace(anchor.bound,
                        micros=(anchor.bound.micros+1) % 1000000)
                diagnostics['expected_event'] = 'group-decision'
                if case == 'anchor_race':
                    if primary == 'complete':
                        primary = 'supervisor-lost'
                    anchor.kill_group(exit_after_verify=True, deadline=cleanup_deadline)
                else:
                    anchor.kill_group()  # Fresh identity; decision BEFORE any reap.
                diagnostics['cleanup_errors'].extend(anchor.monitor_faults)
                diagnostics['anchor_verification'] = anchor.verification
                diagnostics['expected_event'] = 'supervisor-reap'
                anchor.reap(cleanup_deadline)
                diagnostics['expected_event'] = 'group-disappearance'
                confirmed = _group_gone(anchor, child, evidence, cleanup_deadline)
                if not confirmed:
                    raise IdentityUnknown('missing group disappearance proof')
            except BaseException as error:
                # Keep handlers, pipes, Popen and note alive; do not unwind the
                # neutral runtime or release numeric identity ownership.
                cleanup_faults.append('containment-unconfirmed')
                diagnostics['cleanup_errors'].append(wire.exception_evidence(error))
                if anchor is not None:
                    diagnostics['cleanup_errors'].extend(anchor.monitor_faults)
                diagnostics['cleanup_outcome'] = 'unresolved'
                diagnostics['signal'] = signals.first
                diagnostics['anchor_verification'] = anchor.verification if anchor else None
                raise ContainmentUnresolved(primary, {
                    'process': process, 'anchor': anchor, 'note': note,
                    'fds': owned_fds, 'signals': signals, 'runtime': neutral,
                    'child': child, 'evidence': evidence, 'stress': stress,
                    'primary_exception': primary_exception, 'diagnostics': diagnostics,
                    'cleanup_faults': cleanup_faults}) from None
        else:
            confirmed = True
        if confirmed:
            diagnostics['cleanup_outcome'] = 'confirmed' if process is not None else 'no-process'
            if stress is not None:
                try:
                    stress.wait(timeout=max(0, cleanup_deadline-time.monotonic()))
                except BaseException as error:
                    cleanup_faults.append('stress-unreaped')
                    diagnostics['cleanup_errors'].append(wire.exception_evidence(error))
                    diagnostics['cleanup_outcome'] = 'stress-unreaped'
                    diagnostics['signal'] = signals.first
                    raise ContainmentUnresolved(primary, {
                        'process': process, 'anchor': anchor, 'note': note,
                        'fds': owned_fds, 'signals': signals, 'runtime': neutral,
                        'child': child, 'evidence': evidence, 'stress': stress,
                        'primary_exception': primary_exception, 'diagnostics': diagnostics,
                        'cleanup_faults': cleanup_faults}) from None
            secondary_fault = False
            if note is not None:
                try:
                    note.close()
                except BaseException as error:
                    secondary_fault = True
                    cleanup_faults.append('monitor-close')
                    diagnostics['cleanup_errors'].append(wire.exception_evidence(error))
            for fd in owned_fds:
                try:
                    os.close(fd)
                except BaseException as error:
                    secondary_fault = True
                    cleanup_faults.append('endpoint-close')
                    diagnostics['cleanup_errors'].append(wire.exception_evidence(error))
            try:
                neutral.cleanup()
            except BaseException as error:
                secondary_fault = True
                cleanup_faults.append('runtime-cleanup')
                diagnostics['cleanup_errors'].append(wire.exception_evidence(error))
            diagnostics['signal'] = signals.first
            try:
                signals.restore()
            except BaseException as error:
                secondary_fault = True
                cleanup_faults.append('handler-restore')
                diagnostics['cleanup_errors'].append(wire.exception_evidence(error))
            if secondary_fault and primary == 'complete':
                primary = 'protocol-failure'
    if signals.first is not None:
        if primary not in _PRIMARY_FAILURES:
            primary = 'interrupted'
    return Outcome(primary or 'start-failed', confirmed,
                   anchor.bound.data() if anchor else {}, child, evidence,
                   tuple(cleanup_faults) + (tuple(anchor.signal_faults) if anchor else ()),
                   diagnostics)


if __name__ == '__main__':
    raise SystemExit(78)  # No standalone/private CLI entry.
