"""Darwin kernel identity and non-reaping notification for owned fake processes.

proc_pidinfo structures follow the public macOS SDK sys/proc_info.h ABI.
Snapshots detect drift; ownership of an unreaped direct child supplies the pin.
This is not an atomic handle against a hostile same-UID reaper.
"""
import ctypes
from dataclasses import asdict, dataclass
import errno
import math
import os
import select
import signal
import subprocess
import sys
import threading
import time

from . import _git_containment_protocol as wire


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
    """Reviewed per-flavor libproc policy, including live/zombie BSD reads.

    XNU xnu-11417.140.69 proc_info.c requires a nonzero arg to enable
    proc_find_zombref for PROC_PIDTBSDINFO. Every BSD caller (including the
    first read before getsid) needs this to observe live-to-zombie transitions.
    PROC_PIDTASKINFO has a separate contract and keeps arg=0. A successful BSD
    read alone grants no SID, ownership, or signaling authority.
    """
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise NotSupported('Darwin non-root host required')
    if flavor == 3 and model is _BSD:  # PROC_PIDTBSDINFO, allow zombie lookup.
        arg = 1
    elif flavor == 4 and model is _Task:  # PROC_PIDTASKINFO, thread-count premise.
        arg = 0
    else:
        raise NotSupported('unsupported kernel identity query')
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    query = lib.proc_pidinfo
    query.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                      ctypes.c_void_p, ctypes.c_int]
    query.restype = ctypes.c_int
    value = model()
    size = ctypes.sizeof(value)
    ctypes.set_errno(0)
    returned = query(pid, flavor, arg, ctypes.byref(value), size)
    number = ctypes.get_errno()  # Capture immediately, before any other API call.
    if returned != size:
        error = IdentityUnknown('kernel identity unavailable')
        error.libproc = {'flavor': flavor, 'arg': arg, 'requested_bytes': size,
                         'returned_bytes': returned, 'errno': number}
        # A failed BSD call (even ESRCH) is not the getsid ESRCH that permits
        # Anchor's two-row owned-zombie proof. Missing/short rows stay unknown.
        raise error
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


def owned_git_identity(process, supervisor):
    """Observe an unreaped direct Git child, including fast Darwin exit.

    Only the closed ADD supervisor uses this: its trusted fixed Git executable
    inherits the supervisor SID and cannot run repository helpers. A zombie
    lacks getsid; two BSD SZOMB rows prove the owned child's inherited group,
    not a general zombie-session query or signaling authority.
    """
    if process.returncode is not None or supervisor.pid != os.getpid():
        raise IdentityUnknown('kernel identity changed')
    try:
        value = identity(process.pid)
    except ProcessLookupError as error:
        if error.errno != errno.ESRCH:
            raise
        first = _info(process.pid, 3, _BSD)
        second = _info(process.pid, 3, _BSD)
        names = ('pid', 'ppid', 'uid', 'pgid', 'seconds', 'micros')
        if (first.status != 5 or second.status != 5
                or any(getattr(first, k) != getattr(second, k) for k in names)):
            raise IdentityUnknown('kernel identity changed')
        value = Identity(second.pid, second.pgid, supervisor.sid, second.uid,
                         second.ppid, second.seconds, second.micros)
    if (value.pid != process.pid or value.ppid != supervisor.pid
            or value.uid != supervisor.uid or value.pgid != supervisor.pgid
            or value.sid != supervisor.sid):
        raise IdentityUnknown('kernel identity changed')
    return value


def git_inventory(supervisor, child=None):
    """Bounded libproc group/child enumeration, observations never kill authority.

    F1 rejects *any* helper. Static executable-surface checks additionally prove
    no repo-controlled helper can appear/escape between inventory snapshots.
    This is not a process sandbox against hostile same-UID concurrency.
    """
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    query = lib.proc_listpids
    query.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
    query.restype = ctypes.c_int
    def pids(kind, number):
        rows = (ctypes.c_int * 4096)()
        size = ctypes.sizeof(rows)
        ctypes.set_errno(0)
        count = query(kind, number, rows, size)
        error = ctypes.get_errno()
        if error or count < 0 or count >= size or count % ctypes.sizeof(ctypes.c_int):
            raise IdentityUnknown('kernel identity unavailable')
        return {pid for pid in rows[:count//ctypes.sizeof(ctypes.c_int)] if pid > 0}
    # Public libproc PROC_PGRP_ONLY=2, PROC_PPID_ONLY=6.
    members = pids(2, supervisor.pgid)
    expected = {supervisor.pid} | ({child.pid} if child is not None else set())
    descendants = pids(6, child.pid) if child is not None else set()
    children = pids(6, supervisor.pid)
    if (supervisor.pid not in members or members - expected or descendants
            or children - ({child.pid} if child is not None else set())):
        raise IdentityUnknown('kernel identity changed')
    return sorted(members)


class ExitNote:
    def __init__(self, pid):
        self.pid = pid
        self.observed_exit = False
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
        self.observed_exit = self.observed_exit or bool(events)
        return self.observed_exit  # NOTE_EXIT is consumed once, not undone by an empty read.

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
    """A closed owner supplies its strict identity() snapshot captured at launch.

    All callers in this slice retain the actual Popen and capture its launch
    identity before any reap. A caller-supplied/self-consistent numeric snapshot
    from arbitrary embedding code is not this premise or execution authority.
    No method reaps until the group signaling decision has been recorded.
    """
    def __init__(self, process, bound):
        self.process, self.bound = process, bound
        self.decided, self.reaped = False, False
        self.signal_faults = []
        self.monitor_faults = []
        self.verification = None

    def _owned(self):
        bound = self.bound
        if (not isinstance(self.process, subprocess.Popen)
                or self.reaped or self.process.returncode is not None
                or bound.pid != self.process.pid
                or bound.pid != bound.pgid or bound.pid != bound.sid
                or bound.ppid != os.getpid() or bound.uid != os.geteuid()):
            raise IdentityUnknown('supervisor anchor ownership unknown')

    def verify(self, deadline=None):
        """Verify this direct child without consuming its exit status.

        Ordinary identity queries still require getsid. Only ESRCH from that
        query may enter the owned-zombie proof. NOTE_EXIT can precede SZOMB on
        Darwin; matching transitional rows may be rechecked only within the
        caller's existing shared deadline, without any consuming wait. Two fresh
        PROC_PIDTBSDINFO rows must say SZOMB and match PID/PGID/UID/PPID/kernel start time. A zombie
        cannot change session; the launch-bound session leader's reserved PID
        pins its equal PGID/SID until this Popen child is reaped. This relies on
        the closed, single-thread, no-foreign-reaper host described in host_check.
        It is not a SID query for arbitrary zombies; e_tpgid is never a SID.
        """
        self.verification = None
        if deadline is not None and (type(deadline) not in (int, float)
                                     or not math.isfinite(deadline)):
            raise IdentityUnknown('shared synthetic cleanup deadline required')
        def in_time():
            if deadline is not None and time.monotonic() >= deadline:
                raise IdentityUnknown('owned zombie continuity unconfirmed')
        in_time()
        self._owned()
        host_check()
        bound = self.bound
        try:
            observed = identity(bound.pid)
        except ProcessLookupError as error:
            if error.errno != errno.ESRCH:
                raise
            fields = ('pid', 'pgid', 'uid', 'ppid', 'seconds', 'micros')
            proof = {'mode': 'unconfirmed-zombie', 'sid_source': 'launch-continuity',
                     'sid_query': 'ESRCH', 'kernel_rows': [],
                     'transition_rows': [], 'observations': 0}
            self.verification = proof  # Bounded actual observations, including rejection.
            while True:
                self._owned()
                host_check()
                in_time()
                row = _info(bound.pid, 3, _BSD)
                data = {**{key: getattr(row, key) for key in fields}, 'status': row.status}
                proof['observations'] += 1
                proof['kernel_status'] = row.status
                proof['kernel_rows'] = (proof['kernel_rows'] + [data])[-2:]
                if row.status != 5:
                    proof['transition_rows'] = (proof['transition_rows'] + [data])[-2:]
                # Drift, a failed/short read, or an external reap is never a
                # transition to retry. Status 2 remains observation, not proof.
                if (row.status not in (2, 5)
                        or any(getattr(row, key) != getattr(bound, key) for key in fields)):
                    raise IdentityUnknown('owned zombie continuity unconfirmed') from None
                self._owned()
                in_time()
                if (len(proof['kernel_rows']) == 2
                        and all(item['status'] == 5 for item in proof['kernel_rows'])):
                    break
                if row.status == 2:
                    if deadline is None:
                        raise IdentityUnknown('owned zombie continuity unconfirmed') from None
                    select.select([], [], [], min(.005, max(0, deadline-time.monotonic())))
            proof['mode'] = 'owned-zombie'
        else:
            if observed != bound:
                raise IdentityUnknown('supervisor anchor ownership unknown')
            proof = {'mode': 'ordinary', 'sid_source': 'getsid'}
        # Recheck the Popen pin after all observations, without poll/wait.
        self._owned()
        host_check()
        in_time()
        self.verification = proof
        return bound

    def kill_group(self, *, exit_after_verify=False, deadline=None):
        if type(exit_after_verify) is not bool:
            raise IdentityUnknown('supervisor anchor ownership unknown')
        self.verify(deadline=deadline)
        bound = self.bound
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
            failure = None
            try:
                os.kill(bound.pid, signal.SIGKILL)
                while not note.exited():
                    if time.monotonic() >= race_deadline:
                        raise IdentityUnknown('synthetic anchor exit notification unavailable')
                    select.select([], [], [], .005)
                # Preserve this fixed race's actual-SZOMB assertion without
                # assuming NOTE_EXIT has already reached the final exit state.
                fields = ('pid', 'pgid', 'uid', 'ppid', 'seconds', 'micros')
                rows = []
                while True:
                    self._owned()
                    host_check()
                    if time.monotonic() >= race_deadline:
                        raise IdentityUnknown('synthetic anchor zombie unconfirmed')
                    row = _info(bound.pid, 3, _BSD)
                    rows = (rows + [{**{key: getattr(row, key) for key in fields},
                                     'status': row.status}])[-2:]
                    if (row.status not in (2, 5)
                            or any(getattr(row, key) != getattr(bound, key) for key in fields)):
                        raise IdentityUnknown('synthetic anchor zombie unconfirmed')
                    self._owned()
                    if row.status == 5:
                        break
                    select.select([], [], [], min(.005, max(0, race_deadline-time.monotonic())))
                self.verify(deadline=race_deadline)
                self.verification['race_exit_rows'] = rows
            except BaseException as error:
                failure = error
                raise
            finally:
                try:
                    note.close()
                except BaseException as error:
                    self.monitor_faults.append(wire.exception_evidence(error))
                    if failure is None:
                        raise
        # The closed host has no other reaper. This direct child is still owned,
        # live or zombie; its reserved PID also pins the equal PGID through killpg.
        self._owned()
        host_check()
        if deadline is not None and time.monotonic() >= deadline:
            raise IdentityUnknown('owned zombie continuity unconfirmed')
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
