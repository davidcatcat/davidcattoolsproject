"""Handshake-gated fake supervisor. The supervisor itself pins the owned group.

There is no CLI or product execution entry. Parent loss is a last-act group
SIGKILL, including this supervisor. A live parent decides group cleanup before
reaping its supervisor. Synthetic children never change session or group.
"""
import fcntl
from dataclasses import replace
import os
import select
import signal
import stat
import subprocess
import sys
import time

from . import _git_containment_protocol as wire
from ._git_process_identity import (ExitNote, Identity, IdentityUnknown,
                                   NotSupported, host_check, identity)


def _endpoints():
    endpoints = []
    for text in os.listdir('/dev/fd'):
        try:
            fd = int(text)
            st = os.fstat(fd)
        except (ValueError, OSError):
            continue
        if fd > 2:
            if not stat.S_ISFIFO(st.st_mode):
                wire.reject()
            endpoints.append((fd, fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE,
                              [st.st_dev, st.st_ino]))
    if len(endpoints) != 2 or {e[1] for e in endpoints} != {os.O_RDONLY, os.O_WRONLY}:
        wire.reject()
    control = next(e for e in endpoints if e[1] == os.O_RDONLY)
    status = next(e for e in endpoints if e[1] == os.O_WRONLY)
    for endpoint in endpoints:
        os.set_inheritable(endpoint[0], False)
    return control, status


def _last_act(own):
    try:
        current = identity(os.getpid())
    except (OSError, IdentityUnknown, NotSupported):
        os._exit(79)  # Ambiguity never grants a second numeric-group attempt.
    # Parent death reparents this supervisor. Its own live PID still pins the
    # equal group/session ID; PPID is not stable in the very EOF path we protect.
    if (replace(current, ppid=own.ppid) != own or own.pid != os.getpid()
            or own.pid != own.pgid or own.pid != own.sid):
        # No uncertain numeric-group signal. Parent owns the unresolved fallback.
        os._exit(79)
    os.killpg(own.pgid, signal.SIGKILL)
    os._exit(79)  # SIGKILL should never return; fixed fail-closed backstop.


def _main():
    child = note = None
    own = None
    try:
        if not sys.flags.isolated or len(sys.argv) != 1:
            return 78
        host_check()
        own = identity(os.getpid())
        if own.pid != own.pgid or own.pid != own.sid:
            return 78
        control, status = _endpoints()
        reader = wire.Reader(control[0])
        handshake_deadline = time.monotonic()+2
        start = reader.receive(handshake_deadline)
        if (start['type'] != 'START' or start['pipes'] != [control[2], status[2]]
                or Identity.parse(start['launch']) != own
                or Identity.parse(start['parent']) != identity(os.getppid())):
            wire.reject()
        digest = wire.verify_binding(start['binding'])
        if start['binding']['fixture'] != {'case': start['case'], 'timeout': start['timeout']}:
            wire.reject()
        nonce = start['nonce']
        recorded = [None]
        def record(number, frame):
            if recorded[0] is None:
                recorded[0] = number
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(number, record)
        wire.send(status[0], wire.message('READY', nonce=nonce, digest=digest,
                  supervisor=own.data()), handshake_deadline)
        # Independent second origin/hash check immediately before fixed spawn.
        wire.verify_binding(start['binding'])
        host_check()
        if select.select([control[0]], [], [], 0)[0]:
            pending = reader.receive(time.monotonic()+.1)
            if pending['type'] != 'CANCEL' or pending['nonce'] != nonce:
                wire.reject()
            wire.send(status[0], wire.message('CANCEL_ACK', nonce=nonce),
                      time.monotonic()+1)
            _last_act(own)
        if recorded[0] is not None:
            _last_act(own)
        child = subprocess.Popen([sys.executable, '-I', '-m',
                  'llmtool._git_containment_child', start['case'], 'leader',
                  wire.canonical({'anchor': own.data(), 'peer': own.data(),
                      'binding': start['binding'], 'nonce': nonce}).decode('ascii')],
                  shell=False, close_fds=True, pass_fds=(), start_new_session=False,
                  cwd=os.getcwd(), env=wire.fresh_environment(),
                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE)
        bound = identity(child.pid)
        if bound.pgid != own.pgid or bound.sid != own.sid or bound.ppid != own.pid:
            wire.reject()
        if start['case'] != 'lost_before_identity':
            wire.send(status[0], wire.message('CHILD_IDENTITY', child=bound.data(),
                      nonce=nonce), time.monotonic()+2)
        if start['case'] == 'registration_failure':
            raise OSError('fixed registration failure injection')
        note = ExitNote(child.pid)
        os.set_blocking(child.stdout.fileno(), False)
        os.set_blocking(child.stderr.fileno(), False)
        buffer, evidence, expected = bytearray(), {}, None
        terminal_sent = False
        cancelled = False
        exited, output_count, stderr_count = False, 0, 0
        partial_deadline = None
        deadline = time.monotonic() + start['timeout']
        while True:
            # Always process liveness before child data/terminal delivery.
            if select.select([control[0]], [], [], 0)[0]:
                try:
                    value = reader.read()
                except EOFError:
                    _last_act(own)
                if value is not None:
                    partial_deadline = None
                    if cancelled or value['type'] != 'CANCEL' or value['nonce'] != nonce:
                        wire.reject()
                    cancelled = True
                    wire.send(status[0], wire.message('CANCEL_ACK', nonce=nonce),
                              time.monotonic()+1)
                    _last_act(own)
                elif partial_deadline is None:
                    partial_deadline = time.monotonic()+1
            if partial_deadline is not None and time.monotonic() >= partial_deadline:
                _last_act(own)
            if recorded[0] is not None:
                _last_act(own)
            try:
                stderr_count += len(os.read(child.stderr.fileno(), 65536))
            except BlockingIOError:
                pass  # Raw bytes are immediately discarded, never reported.
            if not evidence:
                try:
                    data = os.read(child.stdout.fileno(), wire.LIMIT+5-len(buffer))
                except BlockingIOError:
                    data = None
                if data:
                    output_count += len(data)
                    buffer.extend(data)
                    if expected is None and len(buffer) >= 4:
                        expected = int.from_bytes(buffer[:4], 'big')
                        if not 0 < expected <= wire.LIMIT:
                            wire.reject()
                    if expected is not None and len(buffer) >= expected+4:
                        evidence = wire.strict_load(bytes(buffer[4:expected+4]))
                        wire.child_evidence(evidence)
                        if (type(evidence) is not dict
                                or set(evidence) != {'child', 'descendants', 'fds'}
                                or Identity.parse(evidence['child']) != bound):
                            wire.reject()
                        for row in evidence['descendants']:
                            item = Identity.parse(row)
                            if item.pgid != own.pgid or item.sid != own.sid:
                                wire.reject()
            elif start['case'] == 'output':
                try:
                    data = os.read(child.stdout.fileno(), 65536)
                    output_count += len(data)
                except BlockingIOError:
                    pass
            exited = note.exited() or exited  # Non-consuming notification.
            if not terminal_sent and evidence:
                case = start['case']
                if case == 'lost_before_identity':
                    # Controlled descendant exists, then supervisor dies without
                    # delivering CHILD_IDENTITY. Parent still owns its anchor.
                    os._exit(77)
                if case == 'drain_failure':
                    raise OSError('fixed internal drain failure injection')
                if case == 'hung':
                    # Fixed fault, reached after a real controlled descendant exists.
                    signal.signal(signal.SIGINT, signal.SIG_IGN)
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    signal.signal(signal.SIGHUP, signal.SIG_IGN)
                    wire.send(status[0], wire.message('TERMINAL', nonce=nonce,
                        outcome='complete', evidence=evidence), time.monotonic()+1)
                    select.select([], [], [], 30)
                    _last_act(own)
                if case == 'malformed_status':
                    os.write(status[0], b'\x00\x00\x00\x00')
                    _last_act(own)
                if case == 'closed_status':
                    os.close(status[0])
                    _last_act(own)
                output_limit = output_count > wire.LIMIT or stderr_count > wire.LIMIT
                if exited or case in ('descendant', 'anchor_race') or output_limit:
                    outcome = 'output-limit' if output_limit else 'complete'
                    # Terminal describes the primary lifecycle observation, NOT a
                    # group-gone claim. Parent finalization still uses the anchor.
                    wire.send(status[0], wire.message('TERMINAL', nonce=nonce,
                        outcome=outcome, evidence=evidence), time.monotonic()+1)
                    terminal_sent = True
            if time.monotonic() >= deadline and not terminal_sent:
                wire.send(status[0], wire.message('TERMINAL', nonce=nonce,
                    outcome='timeout', evidence=evidence), time.monotonic()+1)
                terminal_sent = True
            select.select([control[0]], [], [], .005)
    except BaseException:
        # Before spawn, invalid direct invocation exits without a child. Once
        # spawned, every exception (including EPIPE) kills the self-owned group.
        if child is not None and own is not None:
            _last_act(own)
        return 78
    finally:
        if note is not None:
            note.close()


if __name__ == '__main__':
    raise SystemExit(_main())
