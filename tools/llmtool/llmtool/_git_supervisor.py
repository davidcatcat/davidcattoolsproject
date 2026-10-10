"""Handshake-gated fake supervisor. The supervisor itself pins the owned group.

There is no CLI or product execution entry. Parent loss is a last-act group
SIGKILL, including this supervisor. A live parent decides group cleanup before
reaping its supervisor. Synthetic children never change session or group.
"""
import fcntl
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
    # Live self PID cannot be reused. In this closed single-thread supervisor,
    # no setsid/setpgid or callback can change membership between check and kill.
    # Generic libproc identity includes PPID, which races with parent death and
    # must not gate the final self-owned cleanup.
    pid = os.getpid()
    if (pid != own.pid or own.pid != own.pgid or own.pid != own.sid
            or os.getpgrp() != pid):
        os._exit(79)
    os.killpg(0, signal.SIGKILL)  # Current group, never a remembered numeric ID.
    os._exit(79)  # SIGKILL should never return.


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
        reader = wire.Reader(control[0], validate_message=False)
        handshake_deadline = time.monotonic()+2
        start = reader.receive(handshake_deadline)
        # Product admission is a distinct schema/domain/manifest. It never
        # selects a fake case, fault hook or fake-child executable.
        if (type(start) is dict
                and start.get('domain') == 'A11b.2.2.2/git-add/internal/v1'):
            return _product_main(start, own, control, status)
        wire.validate(start)
        reader.validate_message = True
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


def _product_main(start, own, control, status):
    from . import _git_add_private as private
    from . import _git_process_identity as kernel
    child = note = None
    spawned = False
    request = None
    recorded = [None]
    try:
        private.standalone('_git_supervisor')
        if (set(start) != {'domain', 'type', 'request', 'parent', 'launch', 'pipes'}
                or start['type'] != 'START'
                or start['domain'] != private.DOMAIN
                or start['pipes'] != [control[2], status[2]]
                or Identity.parse(start['launch']) != own
                or Identity.parse(start['parent']) != identity(os.getppid())):
            private.reject()
        request = start['request']
        action = private.validate_frame(request)
        if start['parent']['ppid'] != request['parent']['pid']:
            private.reject()
        private.verify_runtime(request['runtime'])
        nonce = request['nonce']
        def record(number, frame):
            if recorded[0] is None:
                recorded[0] = number
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(number, record)
        reader = private.Reader(control[0])
        def emit(kind, **extra):
            private.send(status[0], {'domain': private.DOMAIN, 'type': kind,
                'nonce': nonce, **extra}, time.monotonic()+1)
        emit('READY', digest=private.digest(request), supervisor=own.data())
        # Supervisor-owned exact final gate, independent of caller and Host.
        kernel.git_inventory(own)  # Missing libproc enumeration refuses pre-Git.
        host_check()
        _, plan = private.verify_request(request)
        private.fixture_check(request['fixture'], action, nonce)
        if Identity.parse(start['parent']) != identity(os.getppid()):
            private.reject()
        if Identity.parse(request['parent']) != identity(request['parent']['pid']):
            private.reject()
        if recorded[0] is not None or select.select([control[0]], [], [], 0)[0]:
            _last_act(own)
        child = subprocess.Popen(plan.future_add_argv, cwd=plan.repository_root,
            env=dict(plan.future_environment), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            close_fds=True, pass_fds=(), start_new_session=False)
        spawned = True
        bound = kernel.owned_git_identity(child, own)
        emit('CHILD_IDENTITY', child=bound.data())
        note = ExitNote(child.pid)
        for stream in (child.stdout, child.stderr):
            os.set_blocking(stream.fileno(), False)
        counts = [0, 0]
        deadline = time.monotonic()+request['timeout']
        while True:
            if select.select([control[0]], [], [], 0)[0] or recorded[0] is not None:
                # Any control bytes (including partial malformed CANCEL) or
                # EOF are cancellation, never a second START or authority.
                _last_act(own)
            kernel.git_inventory(own, bound)
            for i, stream in enumerate((child.stdout, child.stderr)):
                try:
                    counts[i] += len(os.read(stream.fileno(), 65536))
                except BlockingIOError:
                    pass
            if max(counts) > wire.LIMIT or time.monotonic() >= deadline:
                _last_act(own)
            if note.exited():
                row = kernel._info(child.pid, 3, kernel._BSD)
                if row.status != 5:
                    select.select([control[0]], [], [], .005)
                    continue
                if (row.ppid != own.pid or row.pgid != own.pgid
                        or row.uid != own.uid or row.seconds != bound.seconds
                        or row.micros != bound.micros):
                    raise kernel.IdentityUnknown('kernel identity changed')
                # Non-consuming BSD exit status. Git remains unreaped until
                # Host's fresh anchor group decision kills this supervisor.
                code = os.waitstatus_to_exitcode(row.xstatus)
                emit('TERMINAL', code='MUTATION_GIT_EXIT', exit_code=code)
                while True:
                    kernel.git_inventory(own, bound)
                    if recorded[0] is not None:
                        _last_act(own)
                    if select.select([control[0]], [], [], .005)[0]:
                        # Only an exact FINALIZE earns a positive verdict, and
                        # only after one more passing inventory. Any other byte,
                        # partial frame or EOF stays plain cancellation.
                        try:
                            request_final = reader.read()
                        except BaseException:
                            request_final = None
                        if request_final == {'domain': private.DOMAIN,
                                             'type': 'FINALIZE', 'nonce': nonce}:
                            kernel.git_inventory(own, bound)  # Unknown → retained.
                            emit('FINALIZED')
                        _last_act(own)  # Nothing is emitted after this.
            select.select([control[0]], [], [], .005)
    except kernel.IdentityUnknown as original_error:
        if spawned:
            # An escape/identity uncertainty cannot be fixed by killing just
            # the remembered group. Preserve ownership for Host operator-wait.
            try:
                private.send(status[0], {'domain': private.DOMAIN, 'type': 'TERMINAL',
                    'nonce': request['nonce'], 'code': 'MUTATION_STATE_UNKNOWN',
                    'exit_code': None}, time.monotonic()+1)
            except BaseException:
                pass
            while True:
                try:
                    select.select([], [], [], .05)
                except BaseException:
                    pass  # Retain original_error, child, note and endpoints.
        return 78
    except BaseException:
        if spawned:
            _last_act(own)
        if request is not None:
            try:
                private.send(status[0], {'domain': private.DOMAIN, 'type': 'TERMINAL',
                    'nonce': request['nonce'], 'code': 'MUTATION_START_FAILED',
                    'exit_code': None}, time.monotonic()+1)
            except BaseException:
                pass
        return 78


if __name__ == '__main__':
    raise SystemExit(_main())
