"""Fixed engineering processes only. No command, repository or Git input."""
import os
import select
import sys
import time

from . import _git_containment_protocol as wire
from ._git_process_identity import Identity, identity


def _open_fds():
    result = []
    for value in os.listdir('/dev/fd'):
        try:
            fd = int(value)
            st = os.fstat(fd)
            result.append({'fd': fd, 'device': st.st_dev, 'inode': st.st_ino})
        except (ValueError, OSError):
            pass  # Enumeration's transient directory FD has already closed.
    return sorted(result, key=lambda row: row['fd'])


def _read_ready(stream, deadline):
    fd = stream.fileno()
    os.set_blocking(fd, False)
    raw, size = bytearray(), None
    while time.monotonic() < deadline:
        target = 4 if size is None else size+4
        try:
            part = os.read(fd, target-len(raw))
        except BlockingIOError:
            select.select([fd], [], [], min(.01, max(0, deadline-time.monotonic())))
            continue
        if not part:
            wire.reject()
        raw.extend(part)
        if size is None and len(raw) == 4:
            size = int.from_bytes(raw, 'big')
            if not 0 < size <= wire.LIMIT:
                wire.reject()
        elif size is not None and len(raw) == size+4:
            value = wire.strict_load(bytes(raw[4:]))
            wire.child_evidence(value)
            if not value or value['descendants']:
                wire.reject()
            return value
    raise TimeoutError('synthetic descendant readiness deadline')


def _main():
    # Fixed supervisor-generated positional case; there is no arbitrary argv.
    if (not sys.flags.isolated or len(sys.argv) != 4
            or sys.argv[1] not in wire.CASES):
        return 78
    case, role = sys.argv[1:3]
    if role not in ('leader', 'descendant'):
        return 78
    mine = identity(os.getpid())
    anchor = identity(mine.pgid)
    launch = wire.strict_load(sys.argv[3].encode('ascii'))
    if type(launch) is not dict or set(launch) != {'anchor', 'peer', 'binding', 'nonce'}:
        return 78
    wire.hex_digest(launch['nonce'])
    wire.verify_binding(launch['binding'])
    if (Identity.parse(launch['anchor']) != anchor
            or Identity.parse(launch['peer']) != identity(os.getppid())
            or launch['binding']['fixture']['case'] != case):
        return 78
    if anchor.pid != anchor.pgid or anchor.pid != anchor.sid or mine.sid != anchor.sid:
        return 78
    if role == 'leader' and os.getppid() != anchor.pid:
        return 78
    if role == 'descendant' and (mine.ppid == anchor.pid
            or Identity.parse(launch['peer']).pgid != anchor.pgid):
        return 78
    if role == 'descendant':
        # Controlled descendants deliberately retain the supervisor-owned group.
        raw = wire.canonical({'child': mine.data(), 'descendants': [], 'fds': _open_fds()})
        os.write(1, len(raw).to_bytes(4, 'big')+raw)
        select.select([], [], [], 30)
        return 0
    import subprocess
    descendant = None
    descendant_ready = None
    if case in ('descendant', 'leader_exit', 'hung', 'lost_before_identity',
                'malformed_status', 'closed_status', 'unresolved', 'anchor_race'):
        descendant = subprocess.Popen([sys.executable, '-I', '-m',
            'llmtool._git_containment_child', case, 'descendant',
            wire.canonical({**launch, 'peer': mine.data()}).decode('ascii')],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, shell=False, close_fds=True,
            pass_fds=(), start_new_session=False,
            env=wire.fresh_environment(), cwd=os.getcwd())
        try:
            descendant_ready = _read_ready(descendant.stdout, time.monotonic()+2)
            if Identity.parse(descendant_ready['child']) != identity(descendant.pid):
                wire.reject()
        finally:
            descendant.stdout.close()
    row = {'child': mine.data(), 'descendants': [], 'fds': _open_fds()}
    if descendant is not None:
        row['descendants'] = [descendant_ready['child']]
        # Both synthetic processes enumerate their own descriptors. This list
        # contains only FD/device/inode metadata, never descriptor contents.
        row['fds'].extend(descendant_ready['fds'])
    raw = wire.canonical(row)
    os.write(1, len(raw).to_bytes(4, 'big') + raw)
    if case == 'output':
        os.write(1, b'x' * (wire.LIMIT + 1))
    if case == 'stderr_output':
        os.write(2, b'x' * (wire.LIMIT + 1))
    if case in ('exit', 'leader_exit'):
        # os._exit avoids Popen destructor bookkeeping/reap of the descendant.
        os._exit(0)
    time.sleep(30)
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
