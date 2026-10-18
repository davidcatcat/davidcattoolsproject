"""Provider-specific lifecycle invariants without real signals to the test runner."""
import os
import signal
import threading
import unittest
from unittest.mock import Mock, patch

from llmtool.errors import LLMToolError
from llmtool.providers.cli_lifecycle import CLIProcessLifecycle, _Cancelled, terminate


@unittest.skipUnless(os.name == 'posix', 'requires POSIX signals')
class LifecycleStateTests(unittest.TestCase):
    @staticmethod
    def handlers():
        return {name: signal.getsignal(getattr(signal, name))
                for name in ('SIGINT', 'SIGTERM', 'SIGHUP') if hasattr(signal, name)}

    def test_normal_and_exception_restore_exact_previous_handlers(self):
        before = self.handlers()
        with CLIProcessLifecycle('claude-cli'):
            self.assertNotEqual(self.handlers(), before)
        self.assertEqual(self.handlers(), before)
        with self.assertRaisesRegex(ValueError, '^synthetic$'):
            with CLIProcessLifecycle('codex-cli'):
                raise ValueError('synthetic')
        self.assertEqual(self.handlers(), before)

    def test_repeated_signals_only_record_first_and_do_no_cleanup_in_handler(self):
        before = self.handlers()
        with patch('llmtool.providers.cli_lifecycle.terminate') as stop:
            with self.assertRaises(SystemExit) as caught:
                with CLIProcessLifecycle('codex-cli') as lifecycle:
                    process = Mock(returncode=None)
                    lifecycle.own(process)
                    lifecycle._record(signal.SIGTERM, None)
                    lifecycle._record(signal.SIGINT, None)
                    if hasattr(signal, 'SIGHUP'):
                        lifecycle._record(signal.SIGHUP, None)
                    stop.assert_not_called()
                    with self.assertRaises(_Cancelled):
                        lifecycle.check()
            self.assertEqual(caught.exception.code, 143)
            stop.assert_called_once_with(process)
        self.assertEqual(self.handlers(), before)

    def test_sigint_unwinds_as_existing_keyboard_interrupt(self):
        before = self.handlers()
        with self.assertRaises(KeyboardInterrupt):
            with CLIProcessLifecycle('claude-cli') as lifecycle:
                lifecycle._record(signal.SIGINT, None)
                lifecycle.check()
        self.assertEqual(self.handlers(), before)

    def test_reaped_process_never_signals_stale_pid_or_group(self):
        process = Mock(returncode=0, pid=os.getpid())
        with patch('llmtool.providers.cli_lifecycle.os.killpg') as kill:
            terminate(process)
            kill.assert_not_called()
        process.kill.assert_not_called()
        process.wait.assert_not_called()

    def test_unreaped_owned_process_is_killed_before_reaping_and_stop_is_idempotent(self):
        process = Mock(returncode=None, pid=12345)
        order = []
        def wait():
            order.append('wait')
            process.returncode = -signal.SIGKILL
        process.wait.side_effect = wait
        lifecycle = CLIProcessLifecycle('claude-cli')
        lifecycle.own(process)
        with patch('llmtool.providers.cli_lifecycle.os.killpg', side_effect=lambda *args: order.append('kill')) as kill:
            lifecycle.stop()
            lifecycle.stop()
            kill.assert_called_once_with(process.pid, signal.SIGKILL)
        self.assertEqual(order, ['kill', 'wait'])
        process.poll.assert_not_called()

    def test_worker_thread_rejected_before_installing_handlers_or_spawning(self):
        caught = []
        def worker():
            try:
                with CLIProcessLifecycle('codex-cli'):
                    self.fail('worker lifecycle should not start')
            except LLMToolError as exc:
                caught.append(exc)
        with patch('llmtool.providers.cli_lifecycle.signal.signal') as install:
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            install.assert_not_called()
        self.assertEqual(len(caught), 1)
        self.assertEqual(caught[0].diagnostic.code.value, 'PROVIDER_RUNTIME_ERROR')

    def test_custom_child_reaper_rejected_before_lifecycle_or_spawn(self):
        previous = signal.getsignal(signal.SIGCHLD)
        def custom(number, frame):
            pass
        try:
            signal.signal(signal.SIGCHLD, custom)
            with patch('llmtool.providers.cli_lifecycle.signal.signal') as install:
                with patch('subprocess.Popen') as spawn:
                    with self.assertRaises(LLMToolError) as caught:
                        with CLIProcessLifecycle('claude-cli'):
                            spawn()
                    spawn.assert_not_called()
                install.assert_not_called()
            self.assertEqual(caught.exception.diagnostic.code.value, 'PROVIDER_RUNTIME_ERROR')
            self.assertIs(signal.getsignal(signal.SIGCHLD), custom)
        finally:
            signal.signal(signal.SIGCHLD, previous)

    def test_ignored_termination_dispositions_are_retained_and_recorded(self):
        before = self.handlers()
        try:
            for name in before:
                signal.signal(getattr(signal, name), signal.SIG_IGN)
            with CLIProcessLifecycle('claude-cli') as lifecycle:
                for name in before:
                    number = getattr(signal, name)
                    self.assertIs(signal.getsignal(number), signal.SIG_IGN)
                    self.assertIs(lifecycle.previous[number], signal.SIG_IGN)
                lifecycle.check()
                self.assertIsNone(lifecycle.signum)
            self.assertTrue(all(handler is signal.SIG_IGN for handler in self.handlers().values()))
        finally:
            for name, handler in before.items():
                signal.signal(getattr(signal, name), handler)

    def test_default_sigchld_remains_default(self):
        previous = signal.getsignal(signal.SIGCHLD)
        try:
            signal.signal(signal.SIGCHLD, signal.SIG_DFL)
            with CLIProcessLifecycle('claude-cli'):
                self.assertIs(signal.getsignal(signal.SIGCHLD), signal.SIG_DFL)
            self.assertIs(signal.getsignal(signal.SIGCHLD), signal.SIG_DFL)
        finally:
            signal.signal(signal.SIGCHLD, previous)

    def test_ignored_sigchld_scoped_default_and_exact_restore_all_exit_paths(self):
        previous = signal.getsignal(signal.SIGCHLD)
        cases = ('success', 'provider-error', 'timeout', 'popen-failure', 'cleanup-failure',
                 'SIGINT', 'SIGTERM') + (('SIGHUP',) if hasattr(signal, 'SIGHUP') else ())
        try:
            for case in cases:
                with self.subTest(case=case):
                    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
                    before = self.handlers()
                    exception = (KeyboardInterrupt if case == 'SIGINT' else
                                 SystemExit if case in ('SIGTERM', 'SIGHUP') else OSError)
                    def exercise():
                        with CLIProcessLifecycle('codex-cli') as lifecycle:
                            self.assertIs(signal.getsignal(signal.SIGCHLD), signal.SIG_DFL)
                            self.assertIs(lifecycle.previous[signal.SIGCHLD], signal.SIG_IGN)
                            if case.startswith('SIG'):
                                lifecycle._record(getattr(signal, case), None)
                                lifecycle.check()
                            elif case == 'cleanup-failure':
                                lifecycle.own(Mock(returncode=None))
                            elif case == 'popen-failure':
                                import subprocess
                                with patch('subprocess.Popen', side_effect=OSError('synthetic')):
                                    subprocess.Popen(['synthetic'])
                            elif case != 'success':
                                raise OSError(case)
                    def stop(process):
                        self.assertIs(signal.getsignal(signal.SIGCHLD), signal.SIG_DFL)
                        raise OSError('synthetic cleanup')
                    with patch('llmtool.providers.cli_lifecycle.terminate', side_effect=stop):
                        if case == 'success':
                            exercise()
                        else:
                            with self.assertRaises(exception):
                                exercise()
                    self.assertIs(signal.getsignal(signal.SIGCHLD), signal.SIG_IGN)
                    self.assertEqual(self.handlers(), before)
        finally:
            signal.signal(signal.SIGCHLD, previous)

    def test_ignored_sigchld_restored_after_partial_handler_install_failure(self):
        previous = signal.getsignal(signal.SIGCHLD)
        before = self.handlers()
        original, failed = signal.signal, []
        def install(number, handler):
            if number == signal.SIGTERM and not failed:
                failed.append(True)
                raise OSError('synthetic installation')
            return original(number, handler)
        try:
            signal.signal(signal.SIGCHLD, signal.SIG_IGN)
            with patch('llmtool.providers.cli_lifecycle.signal.signal', side_effect=install):
                with self.assertRaises(LLMToolError):
                    with CLIProcessLifecycle('codex-cli'):
                        self.fail('installation failed')
            self.assertIs(signal.getsignal(signal.SIGCHLD), signal.SIG_IGN)
            self.assertEqual(self.handlers(), before)
        finally:
            signal.signal(signal.SIGCHLD, previous)

    def test_partial_handler_install_failure_restores_previous_handlers(self):
        before = self.handlers()
        original, failed = signal.signal, []
        def install(number, handler):
            if number == signal.SIGTERM and not failed:
                failed.append(True)
                raise OSError('DG1_SYNTHETIC_SECRET')
            return original(number, handler)
        with patch('llmtool.providers.cli_lifecycle.signal.signal', side_effect=install):
            with self.assertRaises(LLMToolError) as caught:
                with CLIProcessLifecycle('codex-cli'):
                    self.fail('installation failed')
        self.assertNotIn('DG1_SYNTHETIC_SECRET', str(caught.exception))
        self.assertEqual(self.handlers(), before)


class BoundedWorkerLifecycleTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'requires POSIX process groups')
    def test_reaped_leader_with_live_descendant_keeps_cleanup_unknown(self):
        import select, subprocess, sys, time
        release_read, release_write = os.pipe()
        # Only the test holds the writer; EOF releases the descendant even if
        # assertions fail. The alarm bounds a broken fixture independently.
        descendant_script = ('import os,signal; signal.alarm(5); '
                             'os.read(%d,1)' % release_read)
        script = ('import os,signal,subprocess,sys; signal.alarm(5); '
                  'child=subprocess.Popen([sys.executable,"-B","-I","-c",'
                  + repr(descendant_script) + '],pass_fds=(' + str(release_read) + ',),'
                  'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); '
                  'print(child.pid,flush=True); sys.stdin.buffer.read(1)')
        leader = subprocess.Popen([sys.executable, '-B', '-I', '-c', script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True, pass_fds=(release_read,))
        os.close(release_read)
        lifecycle = CLIProcessLifecycle('claude-cli', deadline=time.monotonic() + 3)
        try:
            lifecycle.own(leader)
            self.assertTrue(select.select([leader.stdout], [], [], 2)[0])
            descendant = int(leader.stdout.readline())
            leader.stdin.write(b'x'); leader.stdin.close()
            leader.wait(timeout=2)
            self.assertEqual(leader.returncode, 0)
            os.kill(descendant, 0)
            actual_killpg = os.killpg
            with patch('llmtool.providers.cli_lifecycle.os.killpg', wraps=actual_killpg) as probe:
                lifecycle.stop()
            self.assertFalse(lifecycle.cleanup_confirmed)
            self.assertTrue(leader._llmtool_cleanup_pending)
            self.assertTrue(all(call.args[1] == 0 for call in probe.call_args_list),
                            'reaped PGID must never receive a terminating signal')
        finally:
            os.close(release_write)
            for stream in (leader.stdin, leader.stdout):
                if stream is not None and not stream.closed:
                    stream.close()
            leader.wait(timeout=5)
            # Read-only probes after release; never kill an orphan by stale ID.
            until = time.monotonic() + 3
            while True:
                try:
                    os.killpg(leader.pid, 0)
                except ProcessLookupError:
                    break
                self.assertLess(time.monotonic(), until, 'fixture group still present')
                time.sleep(0.01)

    def test_reaped_group_absence_is_required_and_never_signals(self):
        from llmtool.providers.cli_lifecycle import bounded_terminate
        import time
        for effect, expected in ((None, False), (PermissionError(), False),
                                 (OSError(), False), (ProcessLookupError(), True)):
            with self.subTest(effect=effect):
                process = Mock(returncode=0, pid=12345)
                process._llmtool_owned_pgid = 12345
                process._llmtool_owner_pid = os.getpid()
                process._llmtool_group_unconfirmed = False
                with patch('llmtool.providers.cli_lifecycle.os.killpg', side_effect=effect) as probe:
                    self.assertIs(bounded_terminate(process, time.monotonic() + 1), expected)
                probe.assert_called_once_with(12345, 0)
                process.kill.assert_not_called()
                process.wait.assert_not_called()
                self.assertIs(process._llmtool_cleanup_pending, not expected)

    def test_kill_and_wait_do_not_confirm_remaining_group_absence(self):
        from llmtool.providers.cli_lifecycle import bounded_terminate
        import time
        for observation in (None, PermissionError(), OSError()):
            with self.subTest(observation=observation):
                process = Mock(returncode=None, pid=12345)
                process._llmtool_owned_pgid = 12345
                process._llmtool_owner_pid = os.getpid()
                process._llmtool_group_unconfirmed = False
                def wait(*, timeout):
                    process.returncode = -signal.SIGKILL
                def signal_or_probe(pgid, sig):
                    if sig == 0 and observation is not None:
                        raise observation
                process.wait.side_effect = wait
                with patch('llmtool.providers.cli_lifecycle.os.killpg',
                           side_effect=signal_or_probe) as calls:
                    self.assertFalse(bounded_terminate(process, time.monotonic() + 1))
                    self.assertEqual([call.args for call in calls.call_args_list],
                                     [(12345, signal.SIGKILL), (12345, 0)])
                    calls.reset_mock()
                    self.assertFalse(bounded_terminate(process, time.monotonic() + 1))
                    calls.assert_called_once_with(12345, 0)
                self.assertTrue(process._llmtool_cleanup_pending)
                process.kill.assert_not_called()

    def test_unowned_and_reaped_never_signal(self):
        from llmtool.providers.cli_lifecycle import bounded_terminate
        import time
        process=Mock(returncode=None,pid=12345)
        with patch('llmtool.providers.cli_lifecycle.os.killpg') as kill:
            self.assertFalse(bounded_terminate(process,time.monotonic()+1))
            kill.assert_not_called();process.wait.assert_not_called()
        process.returncode=0
        with patch('llmtool.providers.cli_lifecycle.os.killpg') as kill:
            self.assertFalse(bounded_terminate(process,time.monotonic()+1))
            kill.assert_not_called()

    def test_unknown_teardown_keeps_pending_ownership_no_unbounded_wait(self):
        from llmtool.providers.cli_lifecycle import bounded_terminate
        import time,subprocess
        process=Mock(returncode=None,pid=12345)
        process._llmtool_owned_pgid=12345
        process._llmtool_owner_pid=os.getpid()
        process.wait.side_effect=subprocess.TimeoutExpired('fake',1)
        with patch('llmtool.providers.cli_lifecycle.os.killpg') as kill:
            self.assertFalse(bounded_terminate(process,time.monotonic()+0.01))
            kill.assert_called_once_with(12345,signal.SIGKILL)
        self.assertTrue(process._llmtool_cleanup_pending)
        self.assertIsNone(process.returncode)
        self.assertGreaterEqual(process.wait.call_args.kwargs['timeout'],0)

    def test_expired_deadline_still_signals_owned_group_with_zero_wait(self):
        from llmtool.providers.cli_lifecycle import bounded_terminate
        import math, subprocess, sys, time
        events = []
        process = Mock(returncode=None, pid=12345)
        process._llmtool_owned_pgid = 12345
        process._llmtool_owner_pid = os.getpid()
        def wait(*, timeout=None):
            events.append(('wait', timeout))
            raise subprocess.TimeoutExpired('fake', timeout)
        process.wait.side_effect = wait
        with patch('llmtool.providers.cli_lifecycle.os.killpg',
                   side_effect=lambda pgid, sig: events.append(('killpg', pgid, sig))):
            self.assertFalse(bounded_terminate(process, time.monotonic() - 1.0))
        self.assertEqual(events, [('killpg', 12345, signal.SIGKILL), ('wait', 0)])
        self.assertTrue(process._llmtool_cleanup_pending)
        self.assertIsNone(process.returncode)

        events.clear()
        reaped = Mock(returncode=None, pid=23456)
        reaped._llmtool_owned_pgid = 23456
        reaped._llmtool_owner_pid = os.getpid()
        def wait_reaped(*, timeout=None):
            events.append(('wait', timeout))
            reaped.returncode = -signal.SIGKILL
            return reaped.returncode
        reaped.wait.side_effect = wait_reaped
        def signal_or_probe(pgid, sig):
            events.append(('killpg', pgid, sig))
            if sig == 0:
                raise ProcessLookupError()
        with patch('llmtool.providers.cli_lifecycle.os.killpg',
                   side_effect=signal_or_probe):
            self.assertTrue(bounded_terminate(reaped, time.monotonic() - 0.5))
        self.assertEqual(events, [('killpg', 23456, signal.SIGKILL), ('wait', 0),
                                  ('killpg', 23456, 0)])
        self.assertFalse(reaped._llmtool_cleanup_pending)

        for bad_deadline in (None, 'bad', math.nan, math.inf, -math.inf):
            bad = Mock(returncode=None, pid=12345)
            bad._llmtool_owned_pgid = 12345
            bad._llmtool_owner_pid = os.getpid()
            with patch('llmtool.providers.cli_lifecycle.os.killpg') as kill:
                self.assertFalse(bounded_terminate(bad, bad_deadline))
                kill.assert_not_called()
            bad.kill.assert_not_called()
            bad.wait.assert_not_called()

        if os.name == 'posix':
            child = subprocess.Popen(
                [sys.executable, '-B', '-I', '-c', 'import time; time.sleep(30)'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True)
            try:
                with CLIProcessLifecycle('claude-cli', deadline=time.monotonic() + 5) as lifecycle:
                    lifecycle.own(child)
                    lifecycle.deadline = time.monotonic() - 1.0
                child.wait(timeout=2)
                self.assertIsNotNone(child.returncode)
                self.assertNotEqual(child.returncode, 0)
            finally:
                for stream in (child.stdin, child.stdout, child.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=2)

    def test_group_eperm_falls_back_to_direct_kill_and_retains_pending_unknown(self):
        from llmtool.providers.cli_lifecycle import bounded_terminate
        import time
        events = []
        process = Mock(returncode=None, pid=12345)
        process._llmtool_owned_pgid = 12345
        process._llmtool_owner_pid = os.getpid()
        process.kill.side_effect = lambda: events.append('kill')
        def wait(*, timeout=None):
            events.append(('wait', timeout))
            process.returncode = -signal.SIGKILL
            return process.returncode
        process.wait.side_effect = wait
        def fail_killpg(pgid, sig):
            events.append(('killpg', pgid, sig))
            raise PermissionError('synthetic EPERM')
        with patch('llmtool.providers.cli_lifecycle.os.killpg', side_effect=fail_killpg):
            self.assertFalse(bounded_terminate(process, time.monotonic() + 0.5))
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0], ('killpg', 12345, signal.SIGKILL))
        self.assertEqual(events[1], 'kill')
        self.assertEqual(events[2][0], 'wait')
        self.assertGreaterEqual(events[2][1], 0)
        self.assertLessEqual(events[2][1], 0.5)
        self.assertTrue(process._llmtool_cleanup_pending)
        self.assertEqual(process.returncode, -signal.SIGKILL)

        with patch('llmtool.providers.cli_lifecycle.os.killpg') as killpg:
            process.kill.reset_mock()
            process.wait.reset_mock()
            self.assertFalse(bounded_terminate(process, time.monotonic() + 0.5))
            killpg.assert_not_called()
            process.kill.assert_not_called()
            process.wait.assert_not_called()
        self.assertTrue(process._llmtool_cleanup_pending)

        failing_fallback = Mock(returncode=None, pid=34567)
        failing_fallback._llmtool_owned_pgid = 34567
        failing_fallback._llmtool_owner_pid = os.getpid()
        failing_fallback.kill.side_effect = PermissionError('fallback EPERM')
        with patch('llmtool.providers.cli_lifecycle.os.killpg',
                   side_effect=PermissionError('group EPERM')):
            self.assertFalse(bounded_terminate(failing_fallback, time.monotonic() + 0.5))
        failing_fallback.kill.assert_called_once_with()
        failing_fallback.wait.assert_not_called()
        self.assertTrue(failing_fallback._llmtool_cleanup_pending)

        unowned = Mock(returncode=None, pid=45678)
        unowned._llmtool_owned_pgid = 45678
        unowned._llmtool_owner_pid = os.getpid() + 1
        with patch('llmtool.providers.cli_lifecycle.os.killpg',
                   side_effect=PermissionError('group EPERM')) as killpg:
            self.assertFalse(bounded_terminate(unowned, time.monotonic() + 0.5))
            killpg.assert_not_called()
        unowned.kill.assert_not_called()
        unowned.wait.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX process groups')
    def test_own_validation_failure_cleans_up_fresh_child_without_group_kill_and_retains_unknown(self):
        import subprocess, sys, time
        for mode in ('mismatch', 'oserror'):
            with self.subTest(mode=mode):
                child = subprocess.Popen(
                    [sys.executable, '-B', '-I', '-c', 'import time; time.sleep(30)'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True)
                try:
                    side_effect = (OSError('synthetic getpgid failure')
                                   if mode == 'oserror' else (lambda pid: pid + 1))
                    with patch('llmtool.providers.cli_lifecycle.os.getpgid', side_effect=side_effect), \
                            patch('llmtool.providers.cli_lifecycle.os.killpg') as killpg:
                        with self.assertRaises(LLMToolError) as caught:
                            with CLIProcessLifecycle('claude-cli', deadline=time.monotonic() + 1.0) as lifecycle:
                                lifecycle.own(child)
                        killpg.assert_not_called()
                    self.assertEqual(caught.exception.diagnostic.code.value, 'PROVIDER_RUNTIME_ERROR')
                    self.assertIsNone(getattr(child, '_llmtool_owned_pgid', None))
                    self.assertEqual(getattr(child, '_llmtool_owned_pid', None), child.pid)
                    self.assertFalse(lifecycle.cleanup_confirmed)
                    self.assertTrue(child._llmtool_cleanup_pending)
                    child.wait(timeout=2)
                    self.assertIsNotNone(child.returncode)
                    self.assertNotEqual(child.returncode, 0)
                    with patch('llmtool.providers.cli_lifecycle.os.killpg') as killpg, \
                            patch.object(child, 'kill') as direct_kill:
                        lifecycle.stop()
                        killpg.assert_not_called()
                        direct_kill.assert_not_called()
                    self.assertFalse(lifecycle.cleanup_confirmed)
                    self.assertTrue(child._llmtool_cleanup_pending)
                finally:
                    for stream in (child.stdin, child.stdout, child.stderr):
                        if stream is not None and not stream.closed:
                            stream.close()
                    if child.poll() is None:
                        child.kill()
                        child.wait(timeout=2)

        for invalid in (
            Mock(returncode=0, pid=12345),
            Mock(returncode=None, pid=1),
            Mock(returncode=None, pid=0),
            Mock(returncode=None, pid=-5),
            Mock(returncode=None, pid='12345'),
        ):
            with patch('llmtool.providers.cli_lifecycle.os.killpg') as killpg:
                with self.assertRaises(LLMToolError):
                    with CLIProcessLifecycle('claude-cli', deadline=time.monotonic() + 1.0) as lifecycle:
                        lifecycle.own(invalid)
                killpg.assert_not_called()
            invalid.kill.assert_not_called()
            invalid.wait.assert_not_called()
            self.assertIsNone(getattr(invalid, '_llmtool_owned_pid', None))
            self.assertIsNone(getattr(invalid, '_llmtool_owned_pgid', None))
            self.assertFalse(lifecycle.cleanup_confirmed)


if __name__ == "__main__":
    unittest.main()
