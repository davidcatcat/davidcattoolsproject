"""Darwin kernel identity and non-reaping notification for owned fake processes.

proc_pidinfo structures follow the public macOS SDK sys/proc_info.h ABI.
Snapshots detect drift; ownership of an unreaped direct child supplies the pin.
This is not an atomic handle against a hostile same-UID reaper.
"""
import ctypes
from dataclasses import asdict, dataclass
import os
import select
import signal
import sys
import threading
import time


class NotSupported(Exception):
    pass


class IdentityUnknown(Exception):
    pass


class _BSD(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in
                ('flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid',
                 'ruid', 'rgid', 'svuid', 'svgid', 'reserved')]
    _fields_ += [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in
                 ('nfiles', 'pgid', 'jobc', 'tdev', 'tpgid')]
    _fields_ += [('nice', ctypes.c_int32), ('seconds', ctypes.c_uint64),
                ('micros', ctypes.c_uint64)]


class _Task(ctypes.Structure):
    _fields_ = [('wide' + str(i), ctypes.c_uint64) for i in range(6)]
    _fields_ += [('narrow' + str(i), ctypes.c_int32) for i in range(12)]


def _info(pid, flavor, model):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise NotSupported('Darwin non-root host required')
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    query = lib.proc_pidinfo
    query.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                      ctypes.c_void_p, ctypes.c_int]
    query.restype = ctypes.c_int
    value = model()
    size = ctypes.sizeof(value)
    if query(pid, flavor, 0, ctypes.byref(value), size) != size:
        raise IdentityUnknown('kernel identity unavailable')
    return value


@dataclass(frozen=True)
class Identity:
    pid: int
    pgid: int
    sid: int
    uid: int
    ppid: int
    seconds: int
    micros: int

    def data(self):
        return asdict(self)

    @classmethod
    def parse(cls, data):
        keys = {'pid', 'pgid', 'sid', 'uid', 'ppid', 'seconds', 'micros'}
        if (type(data) is not dict or set(data) != keys
                or any(type(v) is not int or v < 0 for v in data.values())
                or any(data[k] == 0 for k in ('pid', 'pgid', 'sid', 'seconds'))
                or data['micros'] >= 1000000):
            raise IdentityUnknown('invalid kernel identity')
        return cls(**data)


def identity(pid):
    if type(pid) is not int or pid <= 0:
        raise IdentityUnknown('invalid process identity')
    before = _info(pid, 3, _BSD)  # PROC_PIDTBSDINFO
    sid = os.getsid(pid)
    after = _info(pid, 3, _BSD)
    fields = ('pid', 'ppid', 'uid', 'pgid', 'seconds', 'micros')
    if before.pid != pid or any(getattr(before, k) != getattr(after, k) for k in fields):
        raise IdentityUnknown('kernel identity changed')
    return Identity.parse(dict(pid=after.pid, pgid=after.pgid, sid=sid,
                               uid=after.uid, ppid=after.ppid,
                               seconds=after.seconds, micros=after.micros))


def host_check():
    """Necessary checks, supplemented by the reviewed closed harness host.

    Neither Python nor kernel thread counts forbid a future C extension fork.
    The only caller in this slice is the fixed, synchronous test parent; it has
    no plugin/provider callbacks, native extensions doing asynchronous work,
    custom reaper, or production entry. Future embedding needs separate review.
    """
    if (threading.current_thread() is not threading.main_thread()
            or threading.active_count() != 1
            or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL):
        raise NotSupported('single-thread default-reaper host required')
    if _info(os.getpid(), 4, _Task).narrow9 != 1:
        raise NotSupported('native single-thread host required')
    if not hasattr(select, 'kqueue'):
        raise NotSupported('non-reaping Darwin notification required')


class ExitNote:
    def __init__(self, pid):
        self.pid = pid
        self.queue = select.kqueue()
        try:
            event = select.kevent(pid, filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE,
                fflags=select.KQ_NOTE_EXIT)
            self.queue.control([event], 0, 0)
        except BaseException:
            self.queue.close()
            raise

    def exited(self):
        events = self.queue.control([], 1, 0)
        for event in events:
            if (event.ident != self.pid or event.filter != select.KQ_FILTER_PROC
                    or event.flags & select.KQ_EV_ERROR
                    or not event.fflags & select.KQ_NOTE_EXIT):
                raise IdentityUnknown('invalid non-reaping exit notification')
        return bool(events)

    def close(self):
        self.queue.close()


def absent(pid, group=False):
    try:
        (os.killpg if group else os.kill)(pid, 0)
        return False
    except ProcessLookupError:
        return True
    # EPERM is deliberately not translated to absence.


class Anchor:
    """No method reaps until the group signaling decision has been recorded."""
    def __init__(self, process, bound):
        self.process, self.bound = process, bound
        self.decided, self.reaped = False, False
        self.signal_faults = []

    def kill_group(self, *, exit_after_verify=False, deadline=None):
        bound = self.bound
        if (type(exit_after_verify) is not bool or self.reaped or self.process.returncode is not None
                or bound.pid != self.process.pid
                or bound.pid != bound.pgid or bound.pid != bound.sid
                or bound.ppid != os.getpid() or bound.uid != os.geteuid()
                or identity(bound.pid) != bound):
            raise IdentityUnknown('supervisor anchor ownership unknown')
        if self.decided:
            return  # Idempotent while still pinned; never repeat a signal.
        if exit_after_verify:
            # Fixed harness-only race: kill the directly owned supervisor after
            # the fresh check and observe its zombie WITHOUT reap. This parent
            # still pins its PID and equal PGID throughout the following signal.
            if type(deadline) not in (int, float):
                raise IdentityUnknown('shared synthetic cleanup deadline required')
            race_deadline = min(deadline, time.monotonic()+1)
            if time.monotonic() >= race_deadline:
                raise IdentityUnknown('synthetic anchor deadline expired')
            note = ExitNote(bound.pid)
            try:
                os.kill(bound.pid, signal.SIGKILL)
                while not note.exited():
                    if time.monotonic() >= race_deadline:
                        raise IdentityUnknown('synthetic anchor exit notification unavailable')
                    select.select([], [], [], .005)
                if _info(bound.pid, 3, _BSD).status != 5:
                    raise IdentityUnknown('synthetic anchor zombie unconfirmed')
            finally:
                note.close()
        # The closed host has no other reaper. This direct child is still owned,
        # live or zombie; its reserved PID also pins the equal PGID through killpg.
        try:
            os.killpg(bound.pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError) as error:
            # A zombie-only group may yield EPERM. This is a decision, not proof.
            self.signal_faults.append(type(error).__name__)
            # Still owned and unreaped: a direct leader kill is safe under the
            # same pin, but never constitutes proof of descendant containment.
            try:
                os.kill(bound.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError) as secondary:
                self.signal_faults.append(type(secondary).__name__)
        self.decided = True

    def reap(self, deadline):
        if not self.decided or self.reaped:
            raise IdentityUnknown('reap ordering rejected')
        self.process.wait(timeout=max(0, deadline - time.monotonic()))
        self.reaped = True
