"""Sealed F1 fixture Host. Direct import/-m without private FD3 refuses.

No normal CLI, provider, debug or model entry. UNKNOWN owns resources forever;
external fixture termination never promotes the recorded outcome to success.
"""
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

from . import _git_add_private as private
from . import _git_containment_protocol as wire
from . import _git_process_identity as kernel


class Signals:
    def __init__(self):
        self.first, self.previous = None, {}

    def record(self, number, frame):
        if self.first is None:
            self.first = number

    def install(self):
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            self.previous[number] = signal.getsignal(number)
            signal.signal(number, self.record)

    def restore(self):
        for number, previous in self.previous.items():
            signal.signal(number, previous)


def _emit(value):
    private.send(1, value, time.monotonic()+1)


def _unknown(ownership, request, error):
    # Arguments retain the Popen, anchor, monitors, critical FDs, handlers,
    # request and original exception. No finally, ACK, timer release or reap.
    incident = {'outcome': 'STATE_UNKNOWN', 'code': 'MUTATION_STATE_UNKNOWN',
        'user_action': 'USER_ACTION_REQUIRED', 'containment_confirmed': False,
        'action_digest': request['action_digest'],
        'repository_digest': private.digest(request['fixture']),
        'observed_supervisor': ownership.get('bound').data() if ownership.get('bound') else {},
        'known_containment': 'unconfirmed', 'index_lock_state': 'unverified',
        'primary_error': wire.exception_evidence(error),
        'stop': 'Do not retry, reset, delete locks or assume descendants are gone.'}
    try:
        _emit(incident)
    except BaseException:
        pass
    while True:
        # SIGINT/TERM/HUP handlers only record; an external fixture harness
        # must explicitly terminate this Host. Reporting failure changes none
        # of its retained containment responsibilities.
        try:
            select.select([], [], [], .05)
        except BaseException:
            pass


def _execute(request, action, plan):
    signals = Signals()
    owned = {'fds': [], 'signals': signals, 'process': None, 'anchor': None,
             'note': None, 'bound': None, 'primary': None}
    child = None
    ready = terminal = False
    exit_code, primary = None, 'MUTATION_START_FAILED'
    interrupted, escaped = False, False
    cleanup_errors = []
    signals.install()
    try:
        if signals.first is not None:
            result = private.post_state(action, plan, exit_code=None, interrupted=True)
            result['code'] = 'MUTATION_INTERRUPTED'
            result['primary_error'], result['cleanup_errors'] = None, []
            return result
        parent = kernel.identity(os.getpid())
        cr, cw = os.pipe()
        owned['fds'].extend((cr, cw))
        sr, sw = os.pipe()
        owned['fds'].extend((sr, sw))
        for fd in owned['fds']:
            if os.get_inheritable(fd):
                private.reject()
        pipes = [[os.fstat(fd).st_dev, os.fstat(fd).st_ino] for fd in (cr, sw)]
        process = subprocess.Popen([sys.executable, '-I', '-m', 'llmtool._git_supervisor'],
            close_fds=True, pass_fds=(cr, sw), shell=False, start_new_session=True,
            cwd=request['fixture']['root'], env=wire.fresh_environment(),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        owned['process'] = process
        for fd in (cr, sw):
            os.close(fd)
            owned['fds'].remove(fd)
        bound = kernel.identity(process.pid)
        owned['bound'] = bound
        owned['anchor'] = kernel.Anchor(process, bound)
        owned['note'] = kernel.ExitNote(process.pid)
        deadline = time.monotonic()+2
        private.send(cw, {'domain': private.DOMAIN, 'type': 'START',
            'request': request, 'parent': parent.data(), 'launch': bound.data(),
            'pipes': pipes}, deadline)
        reader = private.Reader(sr)
        ready = terminal = cancelled = False
        stop = deadline+request['timeout']
        while time.monotonic() < stop:
            # Original launcher liveness is independently observed even though
            # its one-shot pipe is closed after the frame.
            try:
                same_parent = kernel.identity(request['parent']['pid']).data() == request['parent']
            except (OSError, kernel.IdentityUnknown):
                same_parent = False
            if (signals.first is not None or not same_parent) and not cancelled:
                primary, interrupted, cancelled = 'MUTATION_INTERRUPTED', True, True
                stop = min(stop, time.monotonic()+1)
                private.send(cw, {'domain': private.DOMAIN, 'type': 'CANCEL',
                    'nonce': request['nonce']}, stop)
            value = reader.read()
            if value is not None:
                if (type(value) is not dict or value.get('domain') != private.DOMAIN
                        or value.get('nonce') != request['nonce']):
                    private.reject()
                kind = value.get('type')
                if kind == 'READY' and not ready and set(value) == {'domain', 'type', 'nonce', 'digest', 'supervisor'}:
                    if (value['digest'] != private.digest(request)
                            or kernel.Identity.parse(value['supervisor']) != bound):
                        private.reject()
                    ready = True
                elif kind == 'CHILD_IDENTITY' and ready and child is None and set(value) == {'domain', 'type', 'nonce', 'child'}:
                    child = kernel.Identity.parse(value['child'])
                    if (child.ppid != bound.pid or child.pgid != bound.pgid
                            or child.sid != bound.sid or child.uid != bound.uid):
                        escaped = True
                        private.reject()
                elif kind == 'TERMINAL' and ready and not terminal and set(value) == {'domain', 'type', 'nonce', 'code', 'exit_code'}:
                    if value['code'] not in ('MUTATION_GIT_EXIT', 'MUTATION_START_FAILED', 'MUTATION_STATE_UNKNOWN'):
                        private.reject()
                    if value['exit_code'] is not None and (type(value['exit_code']) is not int or not -255 <= value['exit_code'] <= 255):
                        private.reject()
                    if value['code'] == 'MUTATION_STATE_UNKNOWN':
                        escaped = True
                        private.reject()
                    if value['code'] == 'MUTATION_GIT_EXIT' and child is None:
                        private.reject()
                    exit_code = value['exit_code']
                    if not interrupted:
                        primary = value['code']
                    terminal = True
                    break
                else:
                    private.reject()
            if owned['note'].exited():
                primary, interrupted = 'MUTATION_SUPERVISOR_LOST', True
                break
            select.select([sr], [], [], .005)
        if not terminal and not interrupted:
            primary, interrupted = 'MUTATION_TIMEOUT', True
    except BaseException as error:
        owned['primary'] = error
        interrupted = True
        if primary == 'MUTATION_START_FAILED' and owned['process'] is not None:
            primary = 'MUTATION_SUPERVISOR_LOST'
    finally:
        if owned['process'] is not None:
            try:
                # After READY a lost/partial status cannot prove that the
                # supervisor did not observe an escape before reporting broke.
                # Retain the anchor, even if the numeric group looks absent.
                if escaped or (ready and not terminal) or owned['anchor'] is None:
                    raise kernel.IdentityUnknown('kernel identity changed')
                cleanup_deadline = time.monotonic()+5
                owned['anchor'].kill_group(deadline=cleanup_deadline)
                owned['anchor'].reap(cleanup_deadline)
                # Passive post-reap observations never authorize a new signal.
                while time.monotonic() < cleanup_deadline:
                    if (kernel.absent(owned['bound'].pgid, group=True)
                            and (child is None or kernel.absent(child.pid))):
                        break
                    select.select([], [], [], .005)
                else:
                    raise kernel.IdentityUnknown('kernel identity unavailable')
            except BaseException as error:
                _unknown(owned, request, owned['primary'] or error)
        # Normal resource release is reachable only with proof of containment.
        for fd in owned['fds']:
            try:
                os.close(fd)
            except BaseException as error:
                cleanup_errors.append(wire.exception_evidence(error))
        if owned['note'] is not None:
            try:
                owned['note'].close()
            except BaseException as error:
                cleanup_errors.append(wire.exception_evidence(error))
        try:
            signals.restore()
        except BaseException as error:
            cleanup_errors.append(wire.exception_evidence(error))
    result = private.post_state(action, plan, exit_code=exit_code,
                               interrupted=interrupted or signals.first is not None or bool(cleanup_errors))
    result['code'] = primary
    result['cleanup_errors'] = cleanup_errors
    result['primary_error'] = (wire.exception_evidence(owned['primary'])
                               if owned['primary'] is not None else None)
    return result


def _main():
    try:
        private.standalone('_git_add_host')
        private.endpoints({private.PRIVATE_FD: os.O_RDONLY})
        st = os.fstat(private.PRIVATE_FD)
        reader = private.Reader(private.PRIVATE_FD)
        deadline = time.monotonic()+2
        request = reader.receive(deadline)
        reader.eof(deadline)  # Exactly one frame; no held-open writer or replay.
        os.close(private.PRIVATE_FD)
        private.validate_frame(request)
        if request['pipe'] != [st.st_dev, st.st_ino]:
            private.reject()
        observed = kernel.identity(os.getppid())
        if observed.data() != request['parent'] or observed.uid != os.geteuid():
            private.reject()
        action, plan = private.verify_request(request)
        if kernel.identity(os.getppid()).data() != request['parent']:
            private.reject()
        _emit(_execute(request, action, plan))
        return 0
    except (kernel.NotSupported, ImportError, AttributeError):
        _emit({'outcome': 'NOT_SUPPORTED', 'containment_confirmed': True})
        return 78
    except BaseException:
        # Admission failure has no supervisor or Git child; never echo input.
        try:
            _emit({'outcome': 'AUTHORITY_REJECTED', 'containment_confirmed': True})
        except BaseException:
            pass
        return 78


if __name__ == '__main__':
    raise SystemExit(_main())
