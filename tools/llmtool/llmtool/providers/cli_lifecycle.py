"""Scoped signal/process ownership for the two synchronous CLI providers only."""
import os
import signal
import threading
import subprocess
import time
import math

from ..errors import coded_error


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
        # cleanup UNKNOWN, and must never regain termination authority.
        absent = False
        if (group_owned and os.name == 'posix' and
                getattr(process, '_llmtool_group_unconfirmed', None) is not True):
            absent = _group_absent(process.pid)
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
                process._llmtool_group_unconfirmed = True
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
    # disappeared. Observe absence without delivering another signal after reap.
    absent = group_owned and os.name == 'posix' and _group_absent(process.pid)
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


class CLIProcessLifecycle:
    def __init__(self, provider_type, *, deadline=None):
        self.component = 'provider.' + provider_type
        self.deadline = deadline
        self.cleanup_confirmed = True
        self.process = None
        self.signum = None
        self.previous = {}

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
        if threading.current_thread() is not threading.main_thread():
            raise coded_error('CLI provider invocation requires the main thread',
                              'PROVIDER_RUNTIME_ERROR', self.component)
        child_handler = signal.getsignal(signal.SIGCHLD) if os.name == 'posix' else None
        if os.name == 'posix' and child_handler not in (signal.SIG_DFL, signal.SIG_IGN):
            # A foreign reaper invalidates the unreaped-child identity pin.
            raise coded_error('CLI provider cannot use a custom child reaper',
                              'PROVIDER_RUNTIME_ERROR', self.component)
        try:
            if child_handler is signal.SIG_IGN:
                # Prevent automatic reaping until the owned child is stopped and
                # waited. Record before installation so setup failures restore it.
                self.previous[signal.SIGCHLD] = child_handler
                signal.signal(signal.SIGCHLD, signal.SIG_DFL)
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
                # Adapters stop the process before their runtime context exits.
                # Only after both cleanups do we apply the host exit semantics.
                if self.signum == signal.SIGINT:
                    raise KeyboardInterrupt() from None
                if self.signum is not None:
                    raise CLIHostTermination(128 + self.signum) from None
