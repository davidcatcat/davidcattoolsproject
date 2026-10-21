"""Scoped signal/process ownership for the two synchronous CLI providers only."""
import os
import signal
import threading
import subprocess
import time
import math
from contextlib import contextmanager

from ..errors import coded_error


_thread_policy = threading.local()
_background_lifecycles = []
_tui_signal_scopes = []
_signal_lock = threading.RLock()
_signal_condition = threading.Condition(_signal_lock)


@contextmanager
def background_cli_invocation():
    """Permit a TUI-owned request thread to run the bounded CLI providers.

    Signal handlers are process-global and Python only lets the main thread
    install them. The TUI handles Ctrl-C as an input event and waits for the
    in-flight request to finish or reach the provider timeout; the provider
    still owns and tears down its child process group in either case.
    """
    depth = getattr(_thread_policy, "background_cli", 0)
    _thread_policy.background_cli = depth + 1
    try:
        yield
    finally:
        _thread_policy.background_cli = depth


@contextmanager
def tui_cli_signal_scope(callback):
    """Route process signals to TUI-owned CLI requests while the UI is active."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("TUI signal scope requires the main thread")
    previous = {}

    def record(signum, frame):
        with _signal_condition:
            if record.signum is None:
                record.signum = signum
            for lifecycle in tuple(_background_lifecycles):
                lifecycle._record(signum, frame)
        try:
            callback(signum)
        except BaseException:
            # A Python signal handler must only record/route; provider cleanup
            # and UI exit occur in their normal control flow.
            pass

    record.signum = None

    try:
        if os.name == "posix":
            child_handler = signal.getsignal(signal.SIGCHLD)
            if child_handler is signal.SIG_IGN:
                # The background provider thread cannot change SIGCHLD itself.
                # Keep the child's wait/ownership semantics by temporarily
                # disabling auto-reap in this main-thread-owned TUI scope.
                previous[signal.SIGCHLD] = child_handler
                signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            if hasattr(signal, name):
                number = getattr(signal, name)
                old_handler = signal.getsignal(number)
                previous[number] = old_handler
                if old_handler is not signal.SIG_IGN:
                    signal.signal(number, record)
        with _signal_condition:
            record.active = True
            _tui_signal_scopes.append(record)
        yield
    finally:
        with _signal_condition:
            record.active = False
            # Keep the main-thread signal and SIGCHLD ownership in place until
            # every worker already enrolled under this scope has stopped and
            # reaped its child. New workers see inactive and fail closed.
            while any(lifecycle._tui_scope is record
                      for lifecycle in _background_lifecycles):
                _signal_condition.wait()
            if record in _tui_signal_scopes:
                _tui_signal_scopes.remove(record)
        for number, handler in previous.items():
            signal.signal(number, handler)


class _Cancelled(BaseException):
    """Unwind collection without being normalized as a provider timeout/error."""


class CLIHostTermination(SystemExit):
    """Owned CLI cancellation after process/runtime cleanup; preserve Unix exit."""


def terminate(process):
    # Never poll/reap before signalling: an unreaped owned child pins its PID/PGID.
    # Once wait has reaped it, a stale PGID must not be used (TR-3 is separate).
    if process.returncode is not None:
        return
    try:
        if os.name == 'posix' and hasattr(os, 'killpg'):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    except PermissionError:
        process.kill()
    process.wait()


def bounded_terminate(process, deadline):
    """Opt-in owned-child teardown. False retains pending ownership, never reuses it.

    The lifecycle assigns the private ownership pin immediately after Popen with
    start_new_session=True. Never poll before kill: an unreaped child pins PGID.
    This is cooperative host ownership, not protection against hostile same-UID code.
    """
    owner_ok = (type(deadline) in (int, float) and math.isfinite(deadline) and
                type(process.pid) is int and process.pid > 1 and
                getattr(process, '_llmtool_owner_pid', None) == os.getpid())
    group_owned = (owner_ok and
                   getattr(process, '_llmtool_owned_pgid', None) == process.pid)
    child_owned = (owner_ok and
                   (group_owned or
                    getattr(process, '_llmtool_owned_pid', None) == process.pid))
    if process.returncode is not None:
        # Reaping only confirms the leader's exit, not its descendants. The
        # old pin never authorizes signalling a potentially recycled PGID.
        # Signal zero is only an existence query; even a recycled group keeps
        # cleanup UNKNOWN, and must never regain termination authority. A single
        # probe would misreport a short-lived descendant (or Darwin's transient
        # EPERM window on a zombie-only group) as still-present, so absence is
        # re-observed with signal zero only until the existing cleanup deadline.
        absent = False
        if (group_owned and os.name == 'posix' and
                getattr(process, '_llmtool_group_unconfirmed', None) is not True):
            absent = _group_absent_until(process.pid, deadline)
        process._llmtool_cleanup_pending = not absent
        return absent
    if not child_owned:
        return False
    process._llmtool_cleanup_pending = True
    if not group_owned:
        process._llmtool_group_unconfirmed = True
    try:
        if group_owned and os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except PermissionError:
                # A fresh EPERM on an owned, unreaped group is Darwin's
                # zombie-only window, not proof that group ownership was lost.
                # Keep the existing pin (a prior UNKNOWN stays UNKNOWN), reap
                # this still-pinned child, then re-observe with signal zero.
                process.kill()
        else:
            process._llmtool_group_unconfirmed = True
            process.kill()
    except ProcessLookupError:
        pass
    except OSError:
        return False
    try:
        process.wait(timeout=max(0, deadline - time.monotonic()))
    except (subprocess.TimeoutExpired, OSError):
        return False
    if process.returncode is None or getattr(process, '_llmtool_group_unconfirmed', None) is True:
        process._llmtool_cleanup_pending = True
        return False
    # A successful kill and wait still do not prove that every group member
    # disappeared. Observe absence without delivering another signal after reap;
    # the newly reaped group gets the same bounded, signal-zero-only
    # re-observation as an already-reaped one.
    absent = (group_owned and os.name == 'posix' and
              _group_absent_until(process.pid, deadline))
    process._llmtool_cleanup_pending = not absent
    return absent


def _group_absent(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        pass  # EPERM and other observation failures remain UNKNOWN.
    return False


_REOBSERVE_GRACE = 1.0
_REOBSERVE_INTERVAL = 0.02


def _group_absent_until(pgid, deadline, interval=_REOBSERVE_INTERVAL, grace=_REOBSERVE_GRACE):
    """Bounded, signal-0-only re-observation of an owned, already-reaped group.

    A leader may be reaped while a short-lived same-group descendant, or Darwin's
    transient EPERM window on a zombie-only group, is still observable. One
    immediate probe would turn that benign race into a permanent false UNKNOWN.
    Re-probe with signal zero only: ESRCH confirms absence; continued presence or
    unobservability stays UNKNOWN. A destructive signal is never sent to a
    stale/reaped PGID. The observation is capped to a small grace (and never past
    the caller's cleanup deadline) so it cannot consume the reserve that sibling
    owned processes still need to be terminated. Callers pass a finite deadline.
    """
    if not (type(deadline) in (int, float) and math.isfinite(deadline)):
        return _group_absent(pgid)
    bound = min(deadline, time.monotonic() + grace)
    while True:
        if _group_absent(pgid):
            return True
        remaining = bound - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(interval, remaining))


class CLIProcessLifecycle:
    def __init__(self, provider_type, *, deadline=None):
        self.component = 'provider.' + provider_type
        self.deadline = deadline
        self.cleanup_confirmed = True
        self.process = None
        self.signum = None
        self.previous = {}
        self._tui_scope = None

    def _record(self, signum, frame):
        # No raising, killing, waiting, I/O or cleanup in a handler. In particular,
        # never unwind Popen between child creation and ownership assignment.
        if self.signum is None:
            self.signum = signum

    def _restore(self):
        for number, handler in self.previous.items():
            signal.signal(number, handler)
        self.previous.clear()

    def __enter__(self):
        background = threading.current_thread() is not threading.main_thread()
        if background and not getattr(_thread_policy, "background_cli", 0):
            raise coded_error('CLI provider invocation requires the main thread',
                              'PROVIDER_RUNTIME_ERROR', self.component)
        child_handler = None
        if not background:
            child_handler = signal.getsignal(signal.SIGCHLD) if os.name == 'posix' else None
            if os.name == 'posix' and child_handler not in (signal.SIG_DFL, signal.SIG_IGN):
                # A foreign reaper invalidates the unreaped-child identity pin.
                raise coded_error('CLI provider cannot use a custom child reaper',
                                  'PROVIDER_RUNTIME_ERROR', self.component)
        try:
            if not background and child_handler is signal.SIG_IGN:
                # Prevent automatic reaping until the owned child is stopped and
                # waited. Record before installation so setup failures restore it.
                self.previous[signal.SIGCHLD] = child_handler
                signal.signal(signal.SIGCHLD, signal.SIG_DFL)
            if not background:
                for name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
                    if hasattr(signal, name):
                        number = getattr(signal, name)
                        previous = signal.getsignal(number)
                        self.previous[number] = previous
                        if previous is not signal.SIG_IGN:
                            signal.signal(number, self._record)
        except (OSError, ValueError):
            self._restore()
            raise coded_error('CLI provider signal lifecycle is not available',
                              'PROVIDER_RUNTIME_ERROR', self.component) from None
        if background:
            invalid_owner = False
            custom_reaper = False
            with _signal_condition:
                owner = _tui_signal_scopes[-1] if _tui_signal_scopes else None
                if owner is None or not owner.active:
                    invalid_owner = True
                    pending = None
                else:
                    child_handler = (signal.getsignal(signal.SIGCHLD)
                                     if os.name == 'posix' else None)
                    custom_reaper = (os.name == 'posix' and
                                     child_handler not in (signal.SIG_DFL, signal.SIG_IGN))
                    pending = owner.signum
                if not invalid_owner and not custom_reaper and pending is None:
                    self._tui_scope = owner
                    _background_lifecycles.append(self)
            if invalid_owner:
                raise coded_error('CLI provider background invocation has no active TUI signal owner',
                                  'PROVIDER_RUNTIME_ERROR', self.component)
            if custom_reaper:
                raise coded_error('CLI provider cannot use a custom child reaper',
                                  'PROVIDER_RUNTIME_ERROR', self.component)
            if pending is not None:
                self.signum = pending
                raise _Cancelled()
        return self

    def own(self, process):
        self.process = process
        if self.deadline is not None:
            self.cleanup_confirmed = False
            process._llmtool_owner_pid = None
            process._llmtool_owned_pid = None
            process._llmtool_owned_pgid = None
            process._llmtool_cleanup_pending = True
            process._llmtool_group_unconfirmed = True
            # Only the trusted opt-in launcher uses this seam, immediately after
            # its own start_new_session Popen. A reaped PID is never newly pinned.
            if (type(process.pid) is not int or process.pid <= 1 or
                    process.returncode is not None):
                raise coded_error('CLI child ownership is unknown',
                                  'PROVIDER_RUNTIME_ERROR', self.component)
            process._llmtool_owner_pid = os.getpid()
            process._llmtool_owned_pid = process.pid
            try:
                pgid = os.getpgid(process.pid) if os.name == 'posix' else None
            except OSError:
                pgid = None
            if pgid != process.pid:
                raise coded_error('CLI child ownership is unknown',
                                  'PROVIDER_RUNTIME_ERROR', self.component)
            process._llmtool_owned_pgid = process.pid
            process._llmtool_group_unconfirmed = False
        self.check()

    def check(self):
        if self.signum is not None:
            raise _Cancelled()

    def stop(self):
        if self.process is not None:
            if self.deadline is None:
                terminate(self.process)
            else:
                self.cleanup_confirmed = bounded_terminate(self.process, self.deadline)

    def __exit__(self, kind, value, traceback):
        try:
            self.stop()
        finally:
            try:
                self._restore()
            finally:
                with _signal_condition:
                    if self in _background_lifecycles:
                        _background_lifecycles.remove(self)
                    self._tui_scope = None
                    _signal_condition.notify_all()
                # Adapters stop the process before their runtime context exits.
                # Only after both cleanups do we apply the host exit semantics.
                if self.signum == signal.SIGINT:
                    raise KeyboardInterrupt() from None
                if self.signum is not None:
                    raise CLIHostTermination(128 + self.signum) from None
