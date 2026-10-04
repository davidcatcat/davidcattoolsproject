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
