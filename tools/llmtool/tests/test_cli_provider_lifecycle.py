"""DG-1: real CLI entry point, synthetic process trees, deterministic readiness."""
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import yaml

ROOT = Path(__file__).resolve().parents[3]
PROMPT = 'DG1_SYNTHETIC_PROMPT'
PRIVATE = 'DG1_PRIVATE_STDERR'
SECRET = 'DG1_SYNTHETIC_SECRET'
# Source budget, per invocation (not per full suite): readiness 10s, provider
# default 20s, wrapper drain 5s, disappearance 5s. Double that 40s envelope
# for scheduling margin. Existing product assertions expire BEFORE this guard;
# the guard cannot make a failed 5s termination assertion pass.
FIXTURE_LIFETIME = 2 * (10 + 20 + 5 + 5)


class _FakeTemplate(str):
    def format(self, *args, **kwargs):
        # Persistence regressions also import FAKE; preserve their old call API.
        kwargs.setdefault('lifetime', FIXTURE_LIFETIME)
        return super().format(*args, **kwargs)


FAKE = _FakeTemplate('''#!{python}
import json, os, signal, sys, time
from pathlib import Path
mode = {mode!r}
lifetime = {lifetime!r}
def arm_lifetime():
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {{signal.SIGALRM}})
    signal.alarm(lifetime)
arm_lifetime()  # Before stdin/readiness/release reads, including success mode.
prompt = '' if mode == 'spawn' else sys.stdin.read()
with open({starts!r}, 'a') as f: f.write(str(os.getpid()) + '\\n')
sys.stderr.write('DG1_PRIVATE_STDERR DG1_SYNTHETIC_PROMPT DG1_SYNTHETIC_SECRET reasoning\\n')
sys.stderr.flush()
pids = [os.getpid()]
if mode == 'release': shutdown_read, shutdown_write = os.pipe()
if mode != 'success':
    for name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
        if hasattr(signal, name): signal.signal(getattr(signal, name), signal.SIG_IGN)
    read, write = os.pipe()
    child = os.fork()
    if child == 0:
        arm_lifetime()  # POSIX fork clears pending alarms, not the signal mask.
        os.close(read)
        grandchild = os.fork()
        if grandchild == 0:
            arm_lifetime()
            os.close(write)
            if mode == 'release':
                os.close(shutdown_write)
                os.read(shutdown_read, 1)
                os._exit(0)
            if mode == 'closed':
                for fd in (0, 1, 2): os.close(fd)
            while True: signal.pause()
        os.write(write, (str(os.getpid()) + ' ' + str(grandchild)).encode())
        os.close(write)
        if mode == 'release':
            os.close(shutdown_write)
            os.read(shutdown_read, 1)
            os.waitpid(grandchild, 0)
            os._exit(0)
        if mode == 'closed':
            for fd in (0, 1, 2): os.close(fd)
        while True: signal.pause()
    os.close(write)
    pids.extend(map(int, os.read(read, 256).decode().split()))
    os.close(read)
    if mode == 'release': os.close(shutdown_read)
capture = {{'pids': pids, 'pgid': os.getpgrp(), 'cwd': os.getcwd(),
           'argv': sys.argv[1:], 'secret_inherited': 'OPENAI_API_KEY' in os.environ}}
temporary = Path({capture!r} + '.tmp')
temporary.write_text(json.dumps(capture))
temporary.replace({capture!r})
if mode == 'release':
    while not Path({release!r}).exists(): time.sleep(0.01)
    os.close(shutdown_write)
    os.waitpid(child, 0)
if mode in ('success', 'release'):
    sys.stdout.write('DG1_FINAL\\n')
else:
    if mode == 'closed':
        for fd in (0, 1, 2): os.close(fd)
    while True: signal.pause()
''')


def _bootstrap(function):
    return ('import runpy,sys; namespace=runpy.run_path(%r); '
            'namespace[%r](*sys.argv[1:])') % (str(Path(__file__).resolve()), function)


def _interrupted_runner(directory, fault):
    """Actual unittest runner; its death must bypass its registered cleanup."""
    directory = Path(directory)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGALRM})
    signal.alarm(25)  # Separate runner backstop, even before fixture readiness.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    # No fixture (especially the broken-guard negative) may exist until the
    # watchdog has captured its actual unreaped Popen/group ownership. If the
    # watchdog dies during Popen/adoption, pipe EOF exits BEFORE any fork.
    if sys.stdin.buffer.read(1) != b'R':
        raise AssertionError('runner startup release missing')
    source = FAKE.format(python=sys.executable, mode='waiting', lifetime=3,
        starts=str(directory / 'starts'), capture=str(directory / 'capture.json'),
        release=str(directory / 'release'))
    if fault == 'no-alarm':
        source = source.replace('signal.alarm(lifetime)', 'signal.alarm(0)')
    executable = directory / 'stub.py'
    executable.write_text(source)

    class InterruptedTest(unittest.TestCase):
        def test_owned_fixture(self):
            # Keep the fixtures in this new runner session/group. The external
            # watchdog owns this unreaped session leader, pinning its numeric
            # group throughout fallback signalling (also after runner SIGKILL).
            inherited = ('import os,signal,sys; '
                'signal.signal(signal.SIGALRM,signal.SIG_IGN); '
                'signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM}); '
                'os.execv(sys.executable,[sys.executable,sys.argv[1]])')
            fixture = subprocess.Popen([sys.executable, '-B', '-I', '-c', inherited,
                str(executable)], close_fds=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=directory)
            def cleanup():
                (directory / 'cleanup-ran').write_text('unexpected')
                if fixture.poll() is None:
                    fixture.kill()
                fixture.wait(timeout=5)
            self.addCleanup(cleanup)
            # A real killed runner cannot reach its unittest cleanup.
            while True:
                signal.pause()

    result = unittest.TextTestRunner(stream=sys.stderr).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(InterruptedTest))
    raise SystemExit(0 if result.wasSuccessful() else 1)


def _interruption_watchdog(directory, fault):
    """External owner: deadline and parent-pipe EOF both force pinned teardown."""
    directory = Path(directory)
    runner = None
    verified = decided = False
    report = {'natural_exit': False, 'remaining': None, 'fault': fault}

    def teardown():
        nonlocal decided
        if runner is not None and not decided:
            # No poll/wait before this decision, no custom reaper. An actual
            # unreaped direct Popen child pins PID=PGID=SID; never signal after
            # wait. Captured grandchild numbers are observations, not authority.
            if runner.returncode is not None:
                raise AssertionError('watchdog runner ownership unconfirmed')
            try:
                if verified:
                    os.killpg(runner.pid, signal.SIGKILL)
                else:
                    runner.kill()  # Still gated: no descendants can exist yet.
            except ProcessLookupError:
                pass
            decided = True

    def expire(number, frame):
        teardown()
        os._exit(124)

    def owner_alive():
        if select.select([0], [], [], 0)[0] and not os.read(0, 1):
            raise AssertionError('outer test runner lost')

    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    signal.signal(signal.SIGALRM, expire)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGALRM})
    signal.alarm(23)  # readiness 10 + short guard 3 + observation/reap 5+5.
    try:
        runner = subprocess.Popen([sys.executable, '-B', '-I', '-c',
            _bootstrap('_interrupted_runner'), str(directory), fault],
            start_new_session=True, close_fds=True, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)
        verified = os.getpgid(runner.pid) == os.getsid(runner.pid) == runner.pid
        if not verified:
            raise AssertionError('watchdog session mismatch')
        if fault == 'before-release':
            raise AssertionError('fixed startup-release failure')
        runner.stdin.write(b'R')
        runner.stdin.flush()
        runner.stdin.close()
        deadline = time.monotonic() + 10
        capture = directory / 'capture.json'
        while not capture.exists():
            owner_alive()
            if time.monotonic() >= deadline:
                raise AssertionError('watchdog readiness deadline')
            time.sleep(.01)
        evidence = json.loads(capture.read_text())
        pids = evidence['pids']
        rows = CLIProviderLifecycleTests._running(pids)
        expected_parents = (runner.pid, pids[0], pids[1])
        if (len(pids) != 3 or len(set(pids)) != 3 or set(rows) != set(pids)
                or evidence['pgid'] != runner.pid
                or any(rows[pid]['pgid'] != runner.pid
                       or rows[pid]['ppid'] != parent
                       for pid, parent in zip(pids, expected_parents))):
            raise AssertionError('watchdog tree identity mismatch')
        report.update(pids=pids, pgid=runner.pid, identity_verified=True)
        # Prove intentional polite-signal ignores before killing the TEST runner.
        os.killpg(runner.pid, signal.SIGTERM)  # The owned pin also covers this signal.
        if set(CLIProviderLifecycleTests._running(pids)) != set(pids):
            raise AssertionError('fixture stopped on ignored SIGTERM')
        os.kill(runner.pid, signal.SIGKILL)  # Still-owned, unreaped direct child.
        deadline = time.monotonic() + 4  # 3s per-generation alarm + 1s margin.
        while CLIProviderLifecycleTests._running(pids):
            owner_alive()
            if time.monotonic() >= deadline:
                raise AssertionError('fixture lifetime guard failed')
            time.sleep(.02)
        report['natural_exit'] = True  # BEFORE watchdog's fallback group kill.
    except BaseException as error:
        report['error'] = type(error).__name__
    finally:
        teardown()
        if runner is not None:
            report['runner_exit'] = runner.wait(timeout=5)
            runner.stdin.close()
        if 'pids' in report:
            deadline = time.monotonic() + 5
            while CLIProviderLifecycleTests._running(report['pids']) and time.monotonic() < deadline:
                time.sleep(.02)
            report['remaining'] = CLIProviderLifecycleTests._running(report['pids'])
        report['cleanup_ran'] = (directory / 'cleanup-ran').exists()
        signal.alarm(0)
    print(json.dumps(report), flush=True)
    raise SystemExit(0 if report['natural_exit'] and report['remaining'] == {} else 1)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'fork'), 'requires POSIX CLI process groups')
class CLIProviderLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.home = self.directory / 'home'
        self.bin = self.directory / 'bin'
        self.bin.mkdir()
        self.capture = self.directory / 'capture.json'
        self.starts = self.directory / 'starts'
        self.process = None
        self.evidence = None
        self._fixture_started = None
        self.addCleanup(self.cleanup_owned_fixture)

    @staticmethod
    def states(pids):
        # PID 1 may delay zombie reaping on CI. A zombie cannot execute; never
        # mistake kill(pid, 0) alone for a surviving/running process.
        result = subprocess.run(['/bin/ps', '-o', 'pid=,ppid=,pgid=,stat=', '-p',
                                 ','.join(map(str, pids))], capture_output=True, text=True,
                                timeout=5)
        if result.returncode not in (0, 1) or result.stderr:
            raise AssertionError('process-state probe failed')
        return {int(fields[0]): {'ppid': int(fields[1]), 'pgid': int(fields[2]), 'state': fields[3]}
                for line in result.stdout.splitlines() if len(fields := line.split()) == 4}

    @classmethod
    def _running(cls, pids):
        return {pid: row for pid, row in cls.states(pids).items() if not row['state'].startswith('Z')}

    def assert_fixture_budget(self):
        self.assertIsNotNone(self._fixture_started, 'fixture launch clock missing')
        self.assertLess(time.monotonic() - self._fixture_started, FIXTURE_LIFETIME,
                        'fixture safety guard cannot substitute product cleanup')

    def running(self, pids):
        # Check on BOTH sides of the probe: a suspended runner must not credit
        # alarm-expired descendants as prompt product cleanup. Shared callers
        # use this method too. Cleanup/watchdog observations bypass this lease.
        self.assert_fixture_budget()
        rows = self._running(pids)
        self.assert_fixture_budget()
        return rows

    def cleanup_owned_fixture(self):
        primary = None
        secondary = []
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.kill()
                self.process.communicate(timeout=5)
        except BaseException as error:
            primary = error
        try:
            # A killed wrapper can leave inherited output writers alive. Its
            # drain timeout must not skip the independent owned-group teardown.
            if self.evidence is None and self.capture.exists():
                self.evidence = json.loads(self.capture.read_text())
            if self.evidence:
                alive = self._running(self.evidence['pids'])
                if any(row['pgid'] == self.evidence['pgid'] for row in alive.values()):
                    try:
                        os.killpg(self.evidence['pgid'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        except BaseException as error:
            secondary.append(error)
        finally:
            if self.process is not None:
                try:
                    self.process.wait(timeout=5)
                except BaseException as error:
                    secondary.append(error)
                for stream in (self.process.stdout, self.process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except BaseException as error:
                            secondary.append(error)
            if self.evidence:
                # Only this test's fresh private runtime, never old PIDs or
                # historical/user evidence. Reporting errors cannot skip this.
                try:
                    shutil.rmtree(self.evidence['cwd'], ignore_errors=True)
                except BaseException as error:
                    secondary.append(error)
        if primary is not None:
            primary.fixture_cleanup_errors = tuple(type(error).__name__ for error in secondary)
            raise primary
        if secondary:
            raise secondary[0]

    def launch(self, provider, mode='waiting', timeout=20, phase=None, ignored=None,
               diagnostics=None, flags=()):
        executable = self.bin / provider.split('-')[0]
        executable.write_text(FAKE.format(python=sys.executable, mode=mode,
            lifetime=FIXTURE_LIFETIME,
            starts=str(self.starts), capture=str(self.capture),
            release=str(self.directory / 'release')))
        executable.chmod(0o700)
        config = self.home / '.config/llmtool/config.yaml'
        config.parent.mkdir(parents=True)
        values = {'active_provider': 'synthetic', 'providers': {
            'synthetic': {'type': provider, 'timeout_seconds': timeout, 'model': 'test-model'}}}
        if diagnostics is not None:
            values['diagnostics'] = diagnostics
        config.write_text(yaml.safe_dump(values))
        environment = {'HOME': str(self.home), 'PATH': str(self.bin) + ':/usr/bin:/bin',
            'PYTHONPATH': os.pathsep.join((str(ROOT / 'tools/llmtool'),
                                         str(Path(yaml.__file__).resolve().parent.parent))),
            'PYTHONDONTWRITEBYTECODE': '1', 'OPENAI_API_KEY': SECRET}
        environment['LLMTOOL_HOME'] = str((self.directory / 'state').resolve())
        self.environment = environment
        bootstrap = 'from llmtool.cli import main; raise SystemExit(main())'
        if phase:
            bootstrap = '''
import os, signal, subprocess, time
from pathlib import Path
from unittest.mock import patch
from llmtool.cli import main
phase, capture, waiting = %r, Path(%r), Path(%r)
original_spawn, original_wait = subprocess.Popen, subprocess.Popen.wait
def spawn(*args, **kwargs):
    process = original_spawn(*args, **kwargs)
    if phase == 'spawn':
        deadline = time.monotonic() + 5
        while not capture.exists():
            if time.monotonic() >= deadline: raise AssertionError('fake spawn handshake')
            time.sleep(0.01)
        os.kill(os.getpid(), signal.SIGTERM)
    return process
def wait(process, *args, **kwargs):
    waiting.write_text('waiting')
    result = original_wait(process, *args, **kwargs)
    if phase == 'release-fault':
        os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGINT)
        if hasattr(signal, 'SIGHUP'): os.kill(os.getpid(), signal.SIGHUP)
        raise OSError('DG1_SYNTHETIC_SECRET cleanup fault')
    return result
with patch('subprocess.Popen', side_effect=spawn), patch.object(original_spawn, 'wait', new=wait):
    raise SystemExit(main())
''' % (phase, str(self.capture), str(self.directory / 'waiting'))
        if ignored is None:
            # Test the documented normal dispositions explicitly, even when the
            # runner itself is a non-job-control shell's background job.
            bootstrap = '''
import signal
signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)
if hasattr(signal, 'SIGHUP'): signal.signal(signal.SIGHUP, signal.SIG_DFL)
''' + bootstrap
        else:
            # Exec preserves SIG_IGN: exercise genuine inherited disposition,
            # without preexec_fn in a possibly multithreaded test runner.
            body = '''
import signal
from pathlib import Path
from llmtool.cli import main
try:
    result = main()
finally:
    Path(%r).write_text(str(int(signal.getsignal(%r))))
raise SystemExit(result)
''' % (str(self.directory / 'restored'), int(ignored))
            bootstrap = '''
import os, signal, sys
signal.signal(%r, signal.SIG_IGN)
os.execve(sys.executable, [sys.executable, '-c', %r, 'ask', %r], os.environ)
''' % (int(ignored), body, PROMPT)
        self._fixture_started = time.monotonic()
        self.process = subprocess.Popen([sys.executable, '-c', bootstrap, 'ask', PROMPT, *flags],
            cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 10
        while not self.capture.exists():
            if self.process.poll() is not None or time.monotonic() >= deadline:
                out, err = self.process.communicate(timeout=5)
                self.fail('synthetic provider did not reach readiness: ' + repr((out, err)))
            time.sleep(0.01)  # Readiness handshake, not a guessed signal delay.
        self.evidence = json.loads(self.capture.read_text())
        self.assert_fixture_budget()
        self.assertFalse(self.evidence['secret_inherited'])
        self.assertEqual(self.evidence['pgid'], self.evidence['pids'][0])
        self.assertNotIn(ROOT, Path(self.evidence['cwd']).parents)

    def assert_private_omitted(self, out, err):
        self.assert_fixture_budget()
        for marker in (PROMPT, PRIVATE, SECRET, 'reasoning', 'Traceback'):
            self.assertNotIn(marker.encode(), out + err)
        self.assertEqual(len(self.starts.read_text().splitlines()), 1)

    def interrupted(self, provider, number):
        self.launch(provider)
        self.assertEqual(len(self.evidence['pids']), 3)
        self.assertEqual(set(self.running(self.evidence['pids'])), set(self.evidence['pids']))
        self.process.send_signal(number)
        self.assert_terminated(number)

    def assert_terminated(self, number):
        out, err = self.process.communicate(timeout=5)
        deadline = time.monotonic() + 5
        while self.running(self.evidence['pids']) and time.monotonic() < deadline:
            time.sleep(0.01)
        actual = (self.process.returncode, self.running(self.evidence['pids']),
                  Path(self.evidence['cwd']).exists())
        self.assertEqual(actual, (128 + number, {}, False), json.dumps(self.evidence))
        self.assertEqual(out, b'')
        self.assert_private_omitted(out, err)
        if number == signal.SIGINT:
            self.assertEqual(err, b'llmtool: interrupted\n')
        else:
            self.assertEqual(err, b'')

    def successful(self, provider):
        self.launch(provider, 'success')
        out, err = self.process.communicate(timeout=5)
        self.assertEqual((self.process.returncode, out, err), (0, b'DG1_FINAL\n', b''))
        self.assertFalse(Path(self.evidence['cwd']).exists())
        self.assert_private_omitted(out, err)

    def timed_out(self, provider):
        self.launch(provider, timeout=1)
        out, err = self.process.communicate(timeout=5)
        self.assertEqual((self.process.returncode, out), (1, b''))
        self.assertIn(b'code=PROVIDER_TIMEOUT', err)
        self.assertIn(b'trace=not-saved', err)
        self.assertFalse(Path(self.evidence['cwd']).exists())
        deadline = time.monotonic() + 5
        while self.running(self.evidence['pids']) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.running(self.evidence['pids']), {})
        self.assert_private_omitted(out, err)

    def test_claude_sigterm_tree_and_runtime_cleanup(self):
        self.interrupted('claude-cli', signal.SIGTERM)

    @unittest.skipUnless(hasattr(signal, 'SIGHUP'), 'platform has no SIGHUP')
    def test_claude_sighup_tree_and_runtime_cleanup(self):
        self.interrupted('claude-cli', signal.SIGHUP)

    def test_claude_sigint_contract_and_tree_cleanup(self):
        self.interrupted('claude-cli', signal.SIGINT)

    def test_claude_timeout_contract_and_tree_cleanup(self):
        self.timed_out('claude-cli')

    def test_claude_normal_success_contract(self):
        self.successful('claude-cli')

    def test_codex_sigterm_tree_and_runtime_cleanup(self):
        self.interrupted('codex-cli', signal.SIGTERM)

    @unittest.skipUnless(hasattr(signal, 'SIGHUP'), 'platform has no SIGHUP')
    def test_codex_sighup_tree_and_runtime_cleanup(self):
        self.interrupted('codex-cli', signal.SIGHUP)

    def test_codex_sigint_contract_and_tree_cleanup(self):
        self.interrupted('codex-cli', signal.SIGINT)

    def test_codex_timeout_contract_and_tree_cleanup(self):
        self.timed_out('codex-cli')

    def test_codex_normal_success_contract(self):
        self.successful('codex-cli')

    def inherited_ignore(self, provider, number):
        before = signal.getsignal(number)
        self.launch(provider, 'release', ignored=number)
        self.assertEqual(len(self.evidence['pids']), 3)
        self.process.send_signal(number)
        (self.directory / 'release').write_text('complete')
        out, err = self.process.communicate(timeout=5)
        self.assertEqual((self.process.returncode, out, err), (0, b'DG1_FINAL\n', b''))
        self.assertEqual(self.running(self.evidence['pids']), {})
        # Cooperative successful children/grandchildren are waited/reaped too.
        self.assertEqual(self.states(self.evidence['pids']), {})
        self.assertFalse(Path(self.evidence['cwd']).exists())
        self.assertEqual((self.directory / 'restored').read_text(), str(int(signal.SIG_IGN)))
        self.assertIs(signal.getsignal(number), before)
        self.assert_private_omitted(out, err)

    @unittest.skipUnless(hasattr(signal, 'SIGHUP'), 'platform has no SIGHUP')
    def test_claude_inherited_sighup_ignore_completes_tree_and_runtime(self):
        self.inherited_ignore('claude-cli', signal.SIGHUP)

    @unittest.skipUnless(hasattr(signal, 'SIGHUP'), 'platform has no SIGHUP')
    def test_codex_inherited_sighup_ignore_completes_tree_and_runtime(self):
        self.inherited_ignore('codex-cli', signal.SIGHUP)

    def test_claude_inherited_sigint_ignore_completes_tree_and_runtime(self):
        self.inherited_ignore('claude-cli', signal.SIGINT)

    def test_codex_inherited_sigint_ignore_completes_tree_and_runtime(self):
        self.inherited_ignore('codex-cli', signal.SIGINT)

    def inherited_child_ignore(self, provider):
        before = signal.getsignal(signal.SIGCHLD)
        self.launch(provider, 'release', ignored=signal.SIGCHLD)
        (self.directory / 'release').write_text('complete')
        out, err = self.process.communicate(timeout=5)
        self.assertEqual((self.process.returncode, out, err), (0, b'DG1_FINAL\n', b''))
        self.assertEqual(self.states(self.evidence['pids']), {})
        self.assertFalse(Path(self.evidence['cwd']).exists())
        self.assertEqual((self.directory / 'restored').read_text(), str(int(signal.SIG_IGN)))
        self.assertIs(signal.getsignal(signal.SIGCHLD), before)
        self.assert_private_omitted(out, err)

    def test_claude_inherited_sigchld_ignore_still_works_and_reaps(self):
        self.inherited_child_ignore('claude-cli')

    def test_codex_inherited_sigchld_ignore_still_works_and_reaps(self):
        self.inherited_child_ignore('codex-cli')

    def wait_phase(self, provider):
        self.launch(provider, 'closed', phase='wait')
        deadline = time.monotonic() + 5
        while not (self.directory / 'waiting').exists():
            self.assertIsNone(self.process.poll())
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.process.send_signal(signal.SIGTERM)
        self.assert_terminated(signal.SIGTERM)

    def test_claude_sigterm_after_pipes_close_still_cancels_wait(self):
        self.wait_phase('claude-cli')

    def test_codex_sigterm_after_pipes_close_still_cancels_wait(self):
        self.wait_phase('codex-cli')

    def test_claude_signal_between_spawn_and_ownership_is_not_lost(self):
        self.launch('claude-cli', 'spawn', phase='spawn')
        self.assert_terminated(signal.SIGTERM)

    def test_codex_signal_between_spawn_and_ownership_is_not_lost(self):
        self.launch('codex-cli', 'spawn', phase='spawn')
        self.assert_terminated(signal.SIGTERM)

    def test_claude_cleanup_fault_preserves_signal_exit_and_privacy(self):
        self.launch('claude-cli', phase='release-fault')
        self.process.send_signal(signal.SIGTERM)
        self.assert_terminated(signal.SIGTERM)

    def test_codex_cleanup_fault_preserves_signal_exit_and_privacy(self):
        self.launch('codex-cli', phase='release-fault')
        self.process.send_signal(signal.SIGTERM)
        self.assert_terminated(signal.SIGTERM)

    def test_cleanup_drain_timeout_still_tears_down_owned_group(self):
        for captured in (True, False):
            with self.subTest(captured=captured):
                process = Mock()
                process.poll.return_value = None
                failure = subprocess.TimeoutExpired('synthetic wrapper', 5)
                process.communicate.side_effect = failure
                evidence = {'pids': [123, 124, 125], 'pgid': 123, 'cwd': '/synthetic-runtime'}
                fixture = CLIProviderLifecycleTests('runTest')
                fixture.process = process
                fixture.evidence = evidence if captured else None
                fixture.capture = self.directory / 'mock-capture.json'
                fixture.capture.write_text(json.dumps(evidence))
                with patch.object(fixture, '_running', return_value={123: {'pgid': 123}}), \
                        patch.object(os, 'killpg') as kill, patch.object(shutil, 'rmtree') as remove:
                    with self.assertRaises(subprocess.TimeoutExpired) as raised:
                        fixture.cleanup_owned_fixture()
                    self.assertIs(raised.exception, failure)
                    kill.assert_called_once_with(123, signal.SIGKILL)
                    process.kill.assert_called_once()
                    process.wait.assert_called_once_with(timeout=5)
                    process.stdout.close.assert_called_once()
                    process.stderr.close.assert_called_once()
                    remove.assert_called_once_with('/synthetic-runtime', ignore_errors=True)

    def test_cleanup_never_signals_mismatched_group(self):
        fixture = CLIProviderLifecycleTests('runTest')
        fixture.process = None
        fixture.capture = self.capture
        fixture.evidence = {'pids': [123], 'pgid': 123, 'cwd': '/synthetic-runtime'}
        with patch.object(fixture, '_running', return_value={123: {'pgid': 456}}), \
                patch.object(os, 'killpg') as kill, patch.object(shutil, 'rmtree'):
            fixture.cleanup_owned_fixture()
            kill.assert_not_called()

    def test_cleanup_secondary_failure_preserves_original_drain_timeout(self):
        fixture = CLIProviderLifecycleTests('runTest')
        fixture.capture = self.directory / 'mock-capture.json'
        fixture.evidence = {'pids': [123], 'pgid': 123, 'cwd': '/synthetic-runtime'}
        fixture.process = Mock()
        fixture.process.poll.return_value = None
        failure = subprocess.TimeoutExpired('synthetic wrapper', 5)
        fixture.process.communicate.side_effect = failure
        fixture.process.wait.side_effect = subprocess.TimeoutExpired('synthetic reap', 5)
        with patch.object(fixture, '_running', side_effect=OSError('synthetic inspection')), \
                patch.object(os, 'killpg') as kill, patch.object(shutil, 'rmtree') as remove:
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                fixture.cleanup_owned_fixture()
            self.assertIs(raised.exception, failure)
            self.assertEqual(failure.fixture_cleanup_errors, ('OSError', 'TimeoutExpired'))
            kill.assert_not_called()  # No verified identity, so never guess.
            fixture.process.stdout.close.assert_called_once()
            fixture.process.stderr.close.assert_called_once()
            remove.assert_called_once()

    def test_shared_fake_formatter_keeps_default_and_explicit_lifetime(self):
        arguments = {'python': sys.executable, 'mode': 'waiting', 'starts': str(self.starts),
                     'capture': str(self.capture), 'release': str(self.directory / 'release')}
        self.assertIn('lifetime = 80\n', FAKE.format(**arguments))
        self.assertIn('lifetime = 2\n', FAKE.format(lifetime=2, **arguments))

    def test_expired_fixture_guard_cannot_satisfy_product_cleanup(self):
        fixture = CLIProviderLifecycleTests('runTest')
        fixture._fixture_started = 0
        with patch.object(fixture, '_running', return_value={}):
            for clock in ((80,), (79, 80)):
                with self.subTest(clock=clock), patch.object(time, 'monotonic', side_effect=clock):
                    with self.assertRaisesRegex(AssertionError, 'fixture safety guard'):
                        fixture.running([123])
        with patch.object(time, 'monotonic', return_value=80):
            with self.assertRaisesRegex(AssertionError, 'fixture safety guard'):
                fixture.assert_private_omitted(b'', b'')
        fixture._fixture_started = None
        with self.assertRaisesRegex(AssertionError, 'launch clock missing'):
            fixture.assert_fixture_budget()

    def test_lifetime_guard_covers_blocked_stdin_and_inherited_alarm_state(self):
        executable = self.directory / 'stdin-stub.py'
        executable.write_text(FAKE.format(python=sys.executable, mode='success', lifetime=2,
            starts=str(self.starts), capture=str(self.capture), release=str(self.directory / 'release')))
        inherited = ('import os,signal,sys; signal.signal(signal.SIGALRM,signal.SIG_IGN); '
            'signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM}); '
            'os.execv(sys.executable,[sys.executable,sys.argv[1]])')
        process = subprocess.Popen([sys.executable, '-B', '-I', '-c', inherited, str(executable)],
            start_new_session=True, close_fds=True, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            # Keep stdin OPEN; no prompt/readiness or fork can be reached.
            self.assertEqual(process.wait(timeout=5), -signal.SIGALRM)
            self.assertFalse(self.capture.exists())
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            process.stdin.close()

    def interrupted_runner(self, fault='ordinary'):
        directory = self.directory / fault
        directory.mkdir()
        started = time.monotonic()
        watchdog = subprocess.Popen([sys.executable, '-B', '-I', '-c',
            _bootstrap('_interruption_watchdog'), str(directory), fault],
            start_new_session=True, close_fds=True, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT)
        try:
            # communicate would close its stdin too early: that is the watchdog's
            # owner-liveness pipe. Wait with pipes open; report is bounded JSON.
            watchdog.wait(timeout=28)
            out, err = watchdog.communicate(timeout=5)
        finally:
            if watchdog.stdin is not None:
                watchdog.stdin.close()  # EOF triggers fallback if outer test fails.
            watchdog.wait(timeout=5)
            for stream in (watchdog.stdout, watchdog.stderr):
                stream.close()
        report = json.loads(out)
        self.assertEqual(err, b'')
        self.assertTrue(report['identity_verified'])
        self.assertEqual(report['runner_exit'], -signal.SIGKILL)
        self.assertEqual(report['remaining'], {})  # Zombies cannot execute.
        self.assertFalse(report['cleanup_ran'])
        self.assertEqual(report['natural_exit'], fault == 'ordinary')
        self.assertEqual(watchdog.returncode, 0 if fault == 'ordinary' else 1)
        if fault != 'ordinary':
            self.assertEqual(report['error'], 'AssertionError')
        print('R122 runner interruption: ' + json.dumps({'fault': fault,
            'seconds': round(time.monotonic()-started, 3), 'remaining': report['remaining']}))

    def test_actual_runner_sigkill_all_generations_expire(self):
        self.interrupted_runner()

    def test_external_watchdog_contains_broken_fixture_guard(self):
        self.interrupted_runner('no-alarm')

    def test_watchdog_startup_failure_cannot_launch_unbounded_fixture(self):
        directory = self.directory / 'gated'
        directory.mkdir()
        watchdog = subprocess.Popen([sys.executable, '-B', '-I', '-c',
            _bootstrap('_interruption_watchdog'), str(directory), 'before-release'],
            close_fds=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            watchdog.wait(timeout=28)
            out, err = watchdog.communicate(timeout=5)
        finally:
            watchdog.stdin.close()
            watchdog.wait(timeout=5)
            watchdog.stdout.close()
            watchdog.stderr.close()
        report = json.loads(out)
        self.assertEqual((watchdog.returncode, err, report['runner_exit']),
                         (1, b'', -signal.SIGKILL))
        self.assertFalse((directory / 'starts').exists())
        self.assertFalse((directory / 'capture.json').exists())

    def test_termination_does_not_kill_unrelated_process_group(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
            start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        def cleanup():
            if unrelated.poll() is None:
                unrelated.kill()
            unrelated.wait()
        self.addCleanup(cleanup)
        self.interrupted('codex-cli', signal.SIGTERM)
        self.assertIsNone(unrelated.poll())
        self.assertEqual(os.getpgid(unrelated.pid), unrelated.pid)
