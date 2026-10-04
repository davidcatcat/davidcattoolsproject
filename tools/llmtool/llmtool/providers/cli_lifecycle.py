"""Scoped signal/process ownership for the two synchronous CLI providers only."""
import os
import signal
import threading

from ..errors import coded_error


class _Cancelled(BaseException):
    """Unwind collection without being normalized as a provider timeout/error."""


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


class CLIProcessLifecycle:
    def __init__(self, provider_type):
        self.component = 'provider.' + provider_type
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
        self.check()

    def check(self):
        if self.signum is not None:
            raise _Cancelled()

    def stop(self):
        if self.process is not None:
            terminate(self.process)

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
                    raise SystemExit(128 + self.signum) from None
