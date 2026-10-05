"""DG-1: real CLI entry point, synthetic process trees, deterministic readiness."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
PROMPT = 'DG1_SYNTHETIC_PROMPT'
PRIVATE = 'DG1_PRIVATE_STDERR'
SECRET = 'DG1_SYNTHETIC_SECRET'

FAKE = '''#!{python}
import json, os, signal, sys, time
from pathlib import Path
mode = {mode!r}
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
        os.close(read)
        grandchild = os.fork()
        if grandchild == 0:
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
'''


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
        self.addCleanup(self.cleanup_owned_fixture)

    @staticmethod
    def states(pids):
        # PID 1 may delay zombie reaping on CI. A zombie cannot execute; never
        # mistake kill(pid, 0) alone for a surviving/running process.
        result = subprocess.run(['/bin/ps', '-o', 'pid=,ppid=,pgid=,stat=', '-p',
                                 ','.join(map(str, pids))], capture_output=True, text=True)
        if result.returncode not in (0, 1) or result.stderr:
            raise AssertionError('process-state probe failed')
        return {int(fields[0]): {'ppid': int(fields[1]), 'pgid': int(fields[2]), 'state': fields[3]}
                for line in result.stdout.splitlines() if len(fields := line.split()) == 4}

    @classmethod
    def running(cls, pids):
        return {pid: row for pid, row in cls.states(pids).items() if not row['state'].startswith('Z')}

    def cleanup_owned_fixture(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.communicate(timeout=5)
        if self.evidence is None and self.capture.exists():
            self.evidence = json.loads(self.capture.read_text())
        if self.evidence:
            alive = self.running(self.evidence['pids'])
            if any(row['pgid'] == self.evidence['pgid'] for row in alive.values()):
                try:
                    os.killpg(self.evidence['pgid'], signal.SIGKILL)
                except ProcessLookupError:
                    pass
            # Only this test's freshly captured runtime, never historical DG-1
            # evidence/user artifacts. This cleanup also contains baseline leaks.
            shutil.rmtree(self.evidence['cwd'], ignore_errors=True)

    def launch(self, provider, mode='waiting', timeout=20, phase=None, ignored=None):
        executable = self.bin / provider.split('-')[0]
        executable.write_text(FAKE.format(python=sys.executable, mode=mode,
            starts=str(self.starts), capture=str(self.capture),
            release=str(self.directory / 'release')))
        executable.chmod(0o700)
        config = self.home / '.config/llmtool/config.yaml'
        config.parent.mkdir(parents=True)
        config.write_text(yaml.safe_dump({'active_provider': 'synthetic', 'providers': {
            'synthetic': {'type': provider, 'timeout_seconds': timeout, 'model': 'test-model'}}}))
        environment = {'HOME': str(self.home), 'PATH': str(self.bin) + ':/usr/bin:/bin',
            'PYTHONPATH': os.pathsep.join((str(ROOT / 'tools/llmtool'),
                                         str(Path(yaml.__file__).resolve().parent.parent))),
            'PYTHONDONTWRITEBYTECODE': '1', 'OPENAI_API_KEY': SECRET}
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
        self.process = subprocess.Popen([sys.executable, '-c', bootstrap, 'ask', PROMPT],
            cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 10
        while not self.capture.exists():
            if self.process.poll() is not None or time.monotonic() >= deadline:
                out, err = self.process.communicate(timeout=5)
                self.fail('synthetic provider did not reach readiness: ' + repr((out, err)))
            time.sleep(0.01)  # Readiness handshake, not a guessed signal delay.
        self.evidence = json.loads(self.capture.read_text())
        self.assertFalse(self.evidence['secret_inherited'])
        self.assertEqual(self.evidence['pgid'], self.evidence['pids'][0])
        self.assertNotIn(ROOT, Path(self.evidence['cwd']).parents)

    def assert_private_omitted(self, out, err):
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
