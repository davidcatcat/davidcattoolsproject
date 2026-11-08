import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import dataclasses
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import codex_direct_developer as direct
from llmtool import review_dispatch
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError
from llmtool.worker_dispatch import WorkerDispatchDenied
from llmtool.providers.codex_cli import CodexCLIProvider
from llmtool.providers.cli_lifecycle import CLIHostTermination
from llmtool.cli import parser_for_cli
from llmtool import trust as trust_module


def _dispatcher_trust(root):
    """Patch dispatcher's trust read with fixture repository identity and approvals."""
    from types import MappingProxyType
    fixture = review_trust_fixture.make_trust(
        canonical_root=str(root), workspace_root=str(Path(root).parent / 'workspace'))
    repository = MappingProxyType({
        'slug': fixture.repository['slug'],
        'allowed_origins': fixture.repository['allowed_origins'],
        'approval_refs': fixture.repository['approval_refs'],
    })
    loaded = trust_module.TrustConfig(
        fixture.schema_version, str(root), str(Path(root).parent / 'workspace'),
        str(Path(root).parent / 'workspace' / 'tasks'), repository,
        MappingProxyType({}), MappingProxyType({}), 'a' * 64, 'b' * 64)
    return patch.object(task_submission_cli.trust, 'load_trust', return_value=loaded)


import review_trust_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class CodexDirectDeveloperTests(unittest.TestCase):
    THREAD = '12345678-1234-4234-8234-123456789abc'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='llmtool-codex-direct-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.fake = self.bin / 'codex'
        self.invoked = self.root / 'invoked'
        self.final = {'status': 'DONE', 'summary': 'updated', 'tests': ['passed'],
                      'self_review': 'diff checked', 'blocked_reason': None}
        self.write_fake(self.events(self.final))
        self.path_patch = patch.dict(os.environ, {'PATH': str(self.bin) + os.pathsep + os.environ['PATH']})
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def events(self, final, *, thread_id=None):
        return '\n'.join((
            json.dumps({'type': 'thread.started', 'thread_id': thread_id or self.THREAD}),
            json.dumps({'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': json.dumps(final)}}),
            json.dumps({'type': 'turn.completed', 'usage': {
                'input_tokens': 13, 'cached_input_tokens': 2, 'output_tokens': 8}}),
            '',
        ))

    def write_fake(self, body, *, delay=0, delay_after_output=0,
                   descendant_marker=None, signal_number=None):
        script = "#!/usr/bin/env python3\nimport json,os,sys,time\n"
        script += f"open({str(self.invoked)!r},'w').write('yes')\n"
        script += (
            'expected_cache = "shell_environment_policy.set.PYTHONPYCACHEPREFIX=" + '
            'json.dumps(os.environ["TMPDIR"])\n'
            'assert expected_cache in sys.argv\n')
        if descendant_marker:
            script += (
                'pid=os.fork()\n'
                'if pid == 0:\n'
                ' time.sleep(1)\n'
                f" open({str(descendant_marker)!r},'w').write('survived')\n"
                ' os._exit(0)\n')
        if delay:
            script += f'time.sleep({delay!r})\n'
        script += f"sys.stdout.write({body!r});sys.stdout.flush()\n"
        if signal_number is not None:
            script += f'os.kill(os.getpid(), {signal_number})\n'
        if delay_after_output:
            script += f'time.sleep({delay_after_output!r})\n'
        self.fake.write_text(script, encoding='utf-8')
        self.fake.chmod(0o700)

    def write_fake_script(self, body, *, hold_open=10):
        """Write body as executable fake-process source instead of stdout text."""
        script = "#!/usr/bin/env python3\nimport json,os,signal,sys,time\n"
        script += f"open({str(self.invoked)!r},'w').write('yes')\n"
        script += (
            'expected_cache = "shell_environment_policy.set.PYTHONPYCACHEPREFIX=" + '
            'json.dumps(os.environ["TMPDIR"])\n'
            'assert expected_cache in sys.argv\n')
        script += 'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        script += body if body.endswith('\n') else body + '\n'
        if hold_open:
            script += f'time.sleep({hold_open!r})\n'
        self.fake.write_text(script, encoding='utf-8')
        self.fake.chmod(0o700)

    def run_fake(self, **kwargs):
        timeout = kwargs.pop('timeout', 10)
        prompt = kwargs.pop('prompt', 'bounded prompt')
        kwargs.setdefault('prelaunch_validate', lambda: True)
        with patch('llmtool.codex_direct_developer.worker_dispatch.authorize_dispatch'):
            return direct.run(root=self.root, prompt=prompt, model='gpt-6-luna',
                reasoning_effort='high', dispatch_store=object(), dispatch_task_id='a' * 32,
                timeout=timeout, **kwargs)

    def run_fake_configured(self, configured_timeout):
        with patch('llmtool.codex_direct_developer.codex_direct_timeout',
                   return_value=configured_timeout), \
                patch('llmtool.codex_direct_developer.worker_dispatch.authorize_dispatch'):
            return direct.run(root=self.root, prompt='bounded prompt', model='gpt-6-luna',
                reasoning_effort='high', dispatch_store=object(), dispatch_task_id='a' * 32,
                prelaunch_validate=lambda: True)

    def test_default_timeout_uses_600_seconds(self):
        with patch('llmtool.codex_direct_developer.codex_direct_timeout',
                   return_value=600) as config_loader, \
                patch('llmtool.codex_direct_developer.worker_dispatch.authorize_dispatch'):
            result = direct.run(root=self.root, prompt='bounded prompt', model='gpt-6-luna',
                reasoning_effort='high', dispatch_store=object(), dispatch_task_id='a' * 32,
                prelaunch_validate=lambda: True)
        self.assertEqual(result.status, 'DONE')
        config_loader.assert_called_once_with(cwd=self.root.resolve())

    def test_prompt_at_exact_byte_limit_is_accepted_unchanged(self):
        prompt = 'x' * direct.MAX_PROMPT_BYTES
        self.assertEqual(self.run_fake(prompt=prompt).status, 'DONE')

    def test_configured_timeout_is_used(self):
        self.assertEqual(self.run_fake_configured(900).status, 'DONE')

    def test_invalid_config_timeout_fails_with_typed_config_error(self):
        with patch('llmtool.codex_direct_developer.codex_direct_timeout',
                   side_effect=LLMToolError('invalid direct timeout')), \
                self.assertRaises(LLMToolError) as caught:
            direct.run(root=self.root, prompt='bounded prompt', model='gpt-6-luna',
                reasoning_effort='high', dispatch_store=object(), dispatch_task_id='a' * 32,
                prelaunch_validate=lambda: True)
        self.assertNotIsInstance(caught.exception, direct.DirectCodexError)

    def test_done_captures_thread_usage_and_hardening_arguments(self):
        result = self.run_fake()
        self.assertEqual((result.status, result.thread_id), ('DONE', self.THREAD))
        self.assertEqual(result.usage, {'prompt_tokens': 13, 'completion_tokens': 8,
                                        'total_tokens': 21,
                                        'cached_prompt_tokens': 2})
        self.assertEqual(result.final_message, json.dumps(self.final))
        self.assertTrue(self.invoked.exists())
        argv = direct._argv('/fake/codex', self.root, 'gpt-6-luna', 'high',
                            pycache_prefix='/tmp/codex-direct-cache')
        self.assertNotIn('--ephemeral', argv)
        for control in ('--ignore-user-config', '--ignore-rules', '--strict-config',
                        '--sandbox', 'workspace-write', '--cd', str(self.root)):
            self.assertIn(control, argv)
        self.assertIn('--sandbox', argv)
        self.assertIn('workspace-write', argv)
        self.assertIn('model_reasoning_effort=high', argv)
        self.assertIn('features.remote_plugin=false', argv)
        self.assertIn('web_search="live"', argv)
        for control in ('features.multi_agent=false', 'features.hooks=false',
                        'features.apps=false', 'features.memories=false',
                        'features.remote_plugin=false', 'features.code_mode.enabled=false',
                        'shell_environment_policy.inherit="none"', 'allow_login_shell=false'):
            self.assertIn(control, argv)
        self.assertIn('shell_environment_policy.inherit="none"', argv)
        self.assertIn('shell_environment_policy.set.PYTHONDONTWRITEBYTECODE="1"', argv)
        self.assertIn('shell_environment_policy.set.PYTHONPYCACHEPREFIX="/tmp/codex-direct-cache"',
                      argv)

    def test_role_account_sets_codex_home_without_changing_default_environment(self):
        with patch.dict(os.environ, {'CODEX_HOME': '/inherited/codex'}, clear=False):
            default_env = direct._environment('/tmp/codex-child')
            role_env = direct._environment('/tmp/codex-child', '/role/codex-account')
        self.assertEqual(default_env['CODEX_HOME'], '/inherited/codex')
        self.assertEqual(role_env['CODEX_HOME'], '/role/codex-account')

    def test_launch_callback_receives_child_identity_before_result(self):
        observed = []
        self.write_fake(self.events(self.final), delay_after_output=0.2)
        result = self.run_fake(launch_callback=lambda pid, start, pgid:
                               observed.append((pid, start, pgid)))
        self.assertEqual(result.status, 'DONE')
        self.assertEqual(len(observed), 1)
        pid, start, pgid = observed[0]
        self.assertGreater(pid, 0)
        self.assertTrue(start)
        self.assertEqual(pgid, pid)

    def test_reviewer_argv_remains_ephemeral(self):
        argv = review_dispatch._codex_argv('/fake/codex', self.root)
        self.assertIn('--ephemeral', argv)

    def test_shell_commands_get_an_explicit_path_with_the_worktree_venv_first(self):
        # Regression (#352 G5): with inherit="none" and no PATH, every Codex
        # shell command failed with "command not found" (exit 127).
        argv = direct._argv('/fake/codex', self.root, 'gpt-6-luna', 'high',
                            pycache_prefix='/tmp/codex-direct-cache')
        self.assertIn('shell_environment_policy.set.PATH="/opt/homebrew/bin:/usr/local/bin:'
                      '/usr/bin:/bin"', argv)
        venv_bin = self.root / 'tools' / 'llmtool' / '.venv' / 'bin'
        venv_bin.mkdir(parents=True)
        argv = direct._argv('/fake/codex', self.root, 'gpt-6-luna', 'high',
                            pycache_prefix='/tmp/codex-direct-cache')
        self.assertIn('shell_environment_policy.set.PATH=' + json.dumps(
            str(venv_bin) + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'), argv)
        for bad in ('', 'relative/bin', '/bin:\nx'):
            with self.subTest(path=bad), self.assertRaises(direct.DirectCodexError):
                direct._argv('/fake/codex', self.root, 'gpt-6-luna', 'high',
                             pycache_prefix='/tmp/c', command_path=bad)

    def test_prelaunch_exception_is_typed_and_codex_is_not_started(self):
        self.invoked.unlink(missing_ok=True)
        def stale_binding():
            raise LLMToolError('D6 or Git identity changed before Codex launch')
        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake(prelaunch_validate=stale_binding)
        self.assertEqual(caught.exception.direct_codex_code, 'CODEX_D6_BINDING_CHANGED')
        self.assertIs(caught.exception.attempted, False)
        self.assertFalse(self.invoked.exists())

    def test_unexpected_exception_after_launch_keeps_class_code_and_facts(self):
        with patch.object(direct, '_result_from_events', side_effect=KeyError('secret')):
            with self.assertRaises(direct.DirectCodexError) as caught:
                self.run_fake()
        error = caught.exception
        self.assertEqual(error.direct_codex_code, 'CODEX_ADAPTER_KEY_ERROR')
        self.assertIs(error.attempted, True)
        self.assertEqual(error.process_exit_code, 0)
        self.assertEqual(error.native_session_id, self.THREAD)
        self.assertEqual(error.cleanup_status, 'CONFIRMED')
        self.assertNotIn('secret', str(error))

    def test_adapter_error_codes_are_stable_and_content_free(self):
        coded = LLMToolError('details omitted')
        coded.completion_code = 'D6_SOURCE_BINDING_MISMATCH'
        self.assertEqual(direct.adapter_error_code(coded),
                         'CODEX_ADAPTER_D6_SOURCE_BINDING_MISMATCH')
        self.assertEqual(direct.adapter_error_code(LLMToolError('/some/path')),
                         'CODEX_ADAPTER_LLMTOOL_ERROR')
        self.assertEqual(direct.adapter_error_code(OSError()), 'CODEX_ADAPTER_OSERROR')
        long_name = type('X' * 80 + 'Error', (Exception,), {})()
        self.assertRegex(direct.adapter_error_code(long_name), r'^[A-Z0-9_]{1,64}$')

    def test_blocked_final_is_recorded_as_blocked(self):
        final = dict(self.final, status='BLOCKED', blocked_reason='cannot safely edit')
        self.write_fake(self.events(final))
        self.assertEqual(self.run_fake(timeout=120).status, 'BLOCKED')

    def test_malformed_final_json_is_typed_failure(self):
        raw = json.dumps({'type': 'thread.started', 'thread_id': self.THREAD}) + '\n'
        raw += json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': '{broken'}}) + '\n'
        self.write_fake(raw)
        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake()
        self.assertEqual(caught.exception.direct_codex_code, 'CODEX_FINAL_JSON_INVALID')
        self.assertEqual(caught.exception.native_session_id, self.THREAD)

    def test_invalid_final_json_with_d6_changes_has_distinct_code(self):
        raw = json.dumps({'type': 'thread.started', 'thread_id': self.THREAD}) + '\n'
        raw += json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': '{broken'}}) + '\n'
        self.write_fake(raw)
        with patch.object(direct, '_d6_has_changes', return_value=True), \
                self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake()
        self.assertEqual(caught.exception.direct_codex_code,
                         'CODEX_FINAL_INVALID_WITH_CHANGES')
        self.assertEqual(caught.exception.final_message, '{broken')

    def test_final_json_rejects_extra_keys(self):
        final = dict(self.final, extra='not allowed')
        with self.assertRaises(direct.DirectCodexError) as caught:
            direct._result_from_events(self.events(final).encode('utf-8'))
        self.assertEqual(caught.exception.direct_codex_code,
                         'CODEX_FINAL_JSON_INVALID')

    def test_fenced_final_json_and_object_test_items_are_normalized(self):
        final = dict(self.final, tests=[{'test_one.py': 'passed', 'test_two.py': 'not run'}])
        raw = json.dumps({'type': 'thread.started', 'thread_id': self.THREAD}) + '\n'
        raw += json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': '```json\n' + json.dumps(final) + '\n```'}})
        parsed = direct._result_from_events(raw.encode('utf-8'))
        _, result, _ = parsed
        self.assertEqual(result['tests'], ['test_one.py: passed; test_two.py: not run'])
        self.assertEqual(set(result), set(final))
        self.assertIs(type(result), dict)
        self.assertEqual(parsed.final_message,
                         '```json\n' + json.dumps(final) + '\n```')

    def test_signal_ended_run_without_final_message_is_interrupted(self):
        # Emit malformed partial output before the signal. The interruption
        # outcome must take precedence over JSONL/final-message validation.
        self.write_fake('{partial', signal_number=15)

        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake()

        self.assertEqual(caught.exception.direct_codex_code, 'CODEX_INTERRUPTED')
        self.assertEqual(caught.exception.process_exit_code, -15)

    def test_stdout_cap_error_survives_cleanup_signal_with_observed_facts(self):
        self.write_fake_script("sys.stdout.write('x' * (1024 * 1024 + 1));"
                               "sys.stdout.flush()\n")

        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake()

        error = caught.exception
        self.assertEqual(error.direct_codex_code, 'CODEX_OUTPUT_TOO_LARGE')
        self.assertEqual(error.process_exit_code, -9)
        self.assertEqual(error.cleanup_status, 'CONFIRMED')
        self.assertIsInstance(error.elapsed_ms, int)
        self.assertGreaterEqual(error.elapsed_ms, 0)
        self.assertEqual(error.stdout_sha256,
                         hashlib.sha256(b'x' * direct.MAX_OUTPUT_BYTES).hexdigest())

    def test_stderr_cap_error_survives_cleanup_signal_with_observed_facts(self):
        self.write_fake_script("sys.stderr.write('e' * (64 * 1024 + 1));"
                               "sys.stderr.flush()\n")

        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake()

        error = caught.exception
        self.assertEqual(error.direct_codex_code, 'CODEX_STDERR_TOO_LARGE')
        self.assertEqual(error.process_exit_code, -9)
        self.assertEqual(error.cleanup_status, 'CONFIRMED')
        self.assertIsInstance(error.elapsed_ms, int)
        self.assertGreaterEqual(error.elapsed_ms, 0)
        self.assertEqual(error.stdout_sha256, hashlib.sha256(b'').hexdigest())

    def test_adapter_error_survives_cleanup_signal_with_observed_facts(self):
        self.write_fake('import signal;signal.signal(signal.SIGTERM,signal.SIG_IGN)\n', delay=10)
        original_selector = direct.selectors.DefaultSelector

        class FailingSelector:
            def __init__(self):
                self.selector = original_selector()

            def __getattr__(self, name):
                return getattr(self.selector, name)

            def select(self, wait=None):
                raise RuntimeError('adapter failure')

        with patch.object(direct.selectors, 'DefaultSelector', FailingSelector), \
                self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake()

        error = caught.exception
        self.assertEqual(error.direct_codex_code, 'CODEX_ADAPTER_RUNTIME_ERROR')
        self.assertEqual(error.process_exit_code, -9)
        self.assertEqual(error.cleanup_status, 'CONFIRMED')
        self.assertIsInstance(error.elapsed_ms, int)
        self.assertGreaterEqual(error.elapsed_ms, 0)
        self.assertEqual(error.stdout_sha256, hashlib.sha256(b'').hexdigest())

    def test_partial_jsonl_usage_error_does_not_replace_collection_error(self):
        partial_line = (json.dumps({'type': 'thread.started', 'thread_id': self.THREAD}) +
                        '\n{"type":"turn.completed"').encode('utf-8')
        self.write_fake_script(
            f"sys.stdout.write({partial_line.decode()!r});sys.stdout.flush()\n")
        original_selector = direct.selectors.DefaultSelector

        class FailingAfterOutputSelector:
            def __init__(self):
                self.selector = original_selector()
                self.output_seen = False

            def __getattr__(self, name):
                return getattr(self.selector, name)

            def select(self, wait=None):
                if self.output_seen:
                    raise RuntimeError('adapter failure after partial JSONL')
                events = self.selector.select(wait)
                if any(key.data == 'stdout' for key, _ in events):
                    self.output_seen = True
                return events

        with patch.object(direct.selectors, 'DefaultSelector', FailingAfterOutputSelector), \
                patch.object(direct, '_usage_from_events',
                             side_effect=direct.DirectCodexError('CODEX_EVENT_INVALID')), \
                self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake()

        error = caught.exception
        self.assertEqual(error.direct_codex_code, 'CODEX_ADAPTER_RUNTIME_ERROR')
        self.assertEqual(error.process_exit_code, -9)
        self.assertEqual(error.cleanup_status, 'CONFIRMED')
        self.assertEqual(error.stdout_sha256, hashlib.sha256(partial_line).hexdigest())
        self.assertIsNone(error.usage)

    def test_host_keyboard_interrupt_propagates_from_run(self):
        self.write_fake('', delay=10)
        interrupt = KeyboardInterrupt()
        original_selector = direct.selectors.DefaultSelector

        class InterruptingSelector:
            def __init__(self):
                self.selector = original_selector()

            def __getattr__(self, name):
                return getattr(self.selector, name)

            def select(self, wait=None):
                raise interrupt

        with patch.object(direct.selectors, 'DefaultSelector', InterruptingSelector), \
                self.assertRaises(KeyboardInterrupt) as caught:
            self.run_fake()

        self.assertIs(caught.exception, interrupt)
        self.assertTrue(getattr(interrupt, '_llmtool_direct_codex_execution', None))

    def test_cli_host_termination_propagates_from_run(self):
        self.write_fake('', delay=10)
        termination = CLIHostTermination('host terminated')
        original_selector = direct.selectors.DefaultSelector

        class InterruptingSelector:
            def __init__(self):
                self.selector = original_selector()

            def __getattr__(self, name):
                return getattr(self.selector, name)

            def select(self, wait=None):
                raise termination

        with patch.object(direct.selectors, 'DefaultSelector', InterruptingSelector), \
                self.assertRaises(CLIHostTermination) as caught:
            self.run_fake()

        self.assertIs(caught.exception, termination)
        self.assertTrue(getattr(termination, '_llmtool_direct_codex_execution', None))

    def test_timeout_kills_owned_process_group(self):
        marker = self.root / 'descendant-survived'
        self.write_fake('', delay=10, descendant_marker=marker)
        began = time.monotonic()
        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake(timeout=0.2)
        self.assertEqual(caught.exception.direct_codex_code, 'CODEX_TIMEOUT')
        self.assertEqual(caught.exception.cleanup_status, 'CONFIRMED')
        self.assertLess(time.monotonic() - began, 3)
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_wait_timeout_keeps_observed_thread_usage_and_digest(self):
        # Expire the invocation clock only after stdout has delivered the usage
        # event. This keeps the assertion independent of process startup speed.
        self.write_fake(self.events(self.final), delay_after_output=20)
        timeout = 10
        real_monotonic = time.monotonic
        started = None
        output_seen = False
        original_selector = direct.selectors.DefaultSelector

        class ObservedSelector:
            def __init__(self):
                self.selector = original_selector()

            def __getattr__(self, name):
                return getattr(self.selector, name)

            def select(self, wait=None):
                nonlocal output_seen
                events = self.selector.select(wait)
                output_seen = output_seen or any(key.data == 'stdout' for key, _ in events)
                return events

        def controlled_monotonic():
            nonlocal started
            current = real_monotonic()
            if started is None:
                started = current
            return started + timeout + 1 if output_seen else current

        with patch.object(direct.selectors, 'DefaultSelector', ObservedSelector), \
                patch.object(direct.time, 'monotonic', side_effect=controlled_monotonic), \
                self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake(timeout=timeout)
        self.assertEqual(caught.exception.direct_codex_code, 'CODEX_TIMEOUT')
        self.assertEqual(caught.exception.native_session_id, self.THREAD)
        self.assertEqual(caught.exception.usage, {
            'prompt_tokens': 13, 'completion_tokens': 8, 'total_tokens': 21,
            'cached_prompt_tokens': 2})
        self.assertRegex(caught.exception.stdout_sha256, r'^[0-9a-f]{64}$')

    def test_paused_dispatch_is_refused_before_codex_launch(self):
        self.invoked.unlink(missing_ok=True)
        def paused(*args, **kwargs):
            raise WorkerDispatchDenied('OWNER_PAUSED')
        with patch('llmtool.codex_direct_developer.worker_dispatch.authorize_dispatch', paused):
            with self.assertRaises(direct.DirectCodexError) as caught:
                direct.run(root=self.root, prompt='bounded', model='gpt-6-luna',
                    reasoning_effort='high', dispatch_store=object(),
                    dispatch_task_id='a' * 32, prelaunch_validate=lambda: True, timeout=3)
        self.assertEqual(caught.exception.direct_codex_code, 'OWNER_PAUSED')
        self.assertFalse(self.invoked.exists())

    def test_profile_missing_is_refused(self):
        with patch.object(task_submission_cli, 'resolve_profile_config',
                          side_effect=LLMToolError('missing')):
            with self.assertRaisesRegex(LLMToolError, 'codex-developer profile is missing'):
                task_submission_cli._codex_developer_profile()

    def test_codex_delegate_binds_reported_thread_id_to_executor_record(self):
        task_id = 'a' * 32
        registration_type = dataclasses.make_dataclass('Registration', (
            'task_id trusted_agent_id session_ref native_session_id root common_dir '
            'branch source_sha result_sha').split())
        registration = registration_type(task_id='executor-task',
            trusted_agent_id='codex-developer', session_ref='direct Codex session',
            native_session_id=None, root=str(self.root), common_dir=str(self.root / '.git'),
            branch='task/branch', source_sha='b' * 40, result_sha='b' * 40)
        bound_registration = dataclasses.replace(
            registration, native_session_id=self.THREAD)
        snapshot = SimpleNamespace(root=str(self.root), common_dir=registration.common_dir,
            branch=registration.branch, head='b' * 40)
        request = 'bounded request'
        task = task_submission_cli._record(
            task_id, 'c' * 32, 'pm-session', 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(response='PM response',
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                           'goal': 'selected goal', 'acceptance': ['works']})

        class Store:
            def __init__(self):
                self.value = dict(task)

            def get(self, _task_id):
                return self.value

            def compare_and_update(self, _task_id, expected, **changes):
                self.value.update(changes)
                return self.value

            def d6_bind_compare_and_update(self, task_id, *, executor_task_id,
                                           expected, **changes):
                return self.compare_and_update(task_id, expected, **changes)

            def update(self, _task_id, **changes):
                self.value.update(changes)
                return self.value

        store = Store()
        args = SimpleNamespace(task_id=task_id, context_stdin=False,
            selection_source='PM', acceptance=['works'], executor_task='executor-task',
            read_path=['src.py'], write_path=['src.py'], egress_basis='bounded local code')
        direct_result = SimpleNamespace(status='DONE', thread_id=self.THREAD, exit_code=0,
            stdout_sha256='e' * 64, elapsed_ms=10, cleanup_status='CONFIRMED', usage=None,
            blocked_reason=None)
        with patch.object(task_submission_cli.task_completion, 'validate_task_binding'), \
             patch.object(task_submission_cli.task_completion, 'collect_git_snapshot',
                          return_value=snapshot), \
             patch.object(task_submission_cli.task_completion, 'bind_executor_task',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, '_assignment_paths',
                          return_value=(['src.py'], ['src.py'])), \
             patch.object(task_submission_cli, '_codex_developer_profile',
                          return_value=({'type': 'codex-cli', 'model': 'gpt-6-luna'}, 'high')), \
             patch.object(task_submission_cli, '_codex_direct_prompt', return_value='bounded'), \
             patch.object(task_submission_cli, '_bootstrap_registered_d6'), \
             patch.object(task_submission_cli, '_refresh_d6_binding_after_bootstrap',
                          return_value=registration), \
             patch.object(task_submission_cli, '_ignored_file_fingerprints', return_value={}), \
             patch.object(task_submission_cli.codex_direct_developer, 'run',
                          return_value=direct_result), \
             patch.object(task_submission_cli, '_bind_codex_native_session',
                          return_value='BOUND') as bind, \
             patch.object(task_submission_cli.task_completion, 'read_d6_registration',
                          side_effect=[registration, bound_registration]), \
             patch.object(task_submission_cli.task_completion, '_git', return_value=b''), \
             patch.object(task_submission_cli, '_ignored_output_rejection', return_value=None), \
             patch.object(task_submission_cli, '_codex_cumulative_diff_rejection',
                          return_value=(None, 'diff --git a/src.py b/src.py\n')), \
             patch.object(task_submission_cli, 'run_task_direct_result_event', return_value=0):
            result = task_submission_cli.run_task_codex_delegate(args, store=store)
        self.assertEqual(result, 0)
        bind.assert_called_once_with(registration, self.THREAD)
        self.assertEqual(store.value['developer']['execution']['native_session_binding'], 'BOUND')
        self.assertEqual(store.value['completion']['evidence']['executor_native_session_id'],
                         self.THREAD)
        self.assertEqual(store.value['developer']['direct_result_callback']['evidence'][
            'executor_native_session_id'], self.THREAD)
        self.assertEqual(store.value['developer']['direct_result_callback']['evidence'][
            'executor_native_session'], 'VERIFIED')

    def test_normal_d6_binds_using_pinned_lifecycle_file(self):
        dispatcher = self.root.resolve() / 'dispatcher'
        dispatcher.mkdir()
        lifecycle_rel = 'tools/llmtool/scripts/d6_worktree_lifecycle.py'
        lifecycle = dispatcher / lifecycle_rel
        lifecycle.parent.mkdir(parents=True)
        source_lifecycle = Path(__file__).resolve().parents[1] / 'scripts' / 'd6_worktree_lifecycle.py'
        lifecycle.write_bytes(source_lifecycle.read_bytes())
        dispatcher_file = dispatcher / 'tools/llmtool/llmtool/task_submission_cli.py'
        dispatcher_file.parent.mkdir(parents=True)
        dispatcher_file.write_text('# pinned dispatcher fixture\n', encoding='utf-8')
        env_helper = dispatcher / 'tools/llmtool/scripts/d6_worktree_env.py'
        env_helper.write_text('# pinned helper fixture\n', encoding='utf-8')
        real_run = subprocess.run
        git_env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1',
                       GIT_CONFIG_GLOBAL=os.devnull)
        real_run(['/usr/bin/git', 'init', '-b', 'main'], cwd=dispatcher,
                 env=git_env, check=True, capture_output=True)
        real_run(['/usr/bin/git', 'config', 'user.name', 'Fixture'], cwd=dispatcher,
                 env=git_env, check=True, capture_output=True)
        real_run(['/usr/bin/git', 'config', 'user.email', 'fixture@example.invalid'],
                 cwd=dispatcher, env=git_env, check=True, capture_output=True)
        real_run(['/usr/bin/git', 'add', '.'], cwd=dispatcher, env=git_env,
                 check=True, capture_output=True)
        real_run(['/usr/bin/git', 'commit', '-m', 'pinned dispatcher fixture'],
                 cwd=dispatcher, env=git_env, check=True, capture_output=True)
        real_run(['/usr/bin/git', 'remote', 'add', 'origin',
                  'git@github.com:davidcatcat/davidcattoolsproject.git'],
                 cwd=dispatcher, env=git_env, check=True, capture_output=True)
        registration = SimpleNamespace(task_id='normal-d6', env2_approved_tooling_sha=None)
        invocation = []

        def run(command, **kwargs):
            if command and command[0] == sys.executable:
                invocation.append(command)
                return subprocess.CompletedProcess(command, 0, b'', b'')
            return real_run(command, **kwargs)

        with _dispatcher_trust(dispatcher), \
             patch.object(task_submission_cli, '__file__', str(dispatcher_file)), \
             patch.object(task_submission_cli.subprocess, 'run', side_effect=run):
            result = task_submission_cli._bind_codex_native_session(registration, self.THREAD)

        self.assertEqual(result, 'BOUND')
        self.assertEqual(len(invocation), 1)
        self.assertEqual(invocation[0][3], str(lifecycle))
        self.assertNotIn('-c', invocation[0])

    def test_normal_d6_rejects_lifecycle_mismatch_before_binding(self):
        dispatcher = self.root.resolve() / 'mismatched-dispatcher'
        dispatcher.mkdir()
        lifecycle_rel = 'tools/llmtool/scripts/d6_worktree_lifecycle.py'
        lifecycle = dispatcher / lifecycle_rel
        lifecycle.parent.mkdir(parents=True)
        lifecycle.write_text('# not the pinned source\n', encoding='utf-8')
        dispatcher_file = dispatcher / 'tools/llmtool/llmtool/task_submission_cli.py'
        dispatcher_file.parent.mkdir(parents=True)
        dispatcher_file.write_text('# pinned dispatcher fixture\n', encoding='utf-8')
        env_helper = dispatcher / 'tools/llmtool/scripts/d6_worktree_env.py'
        env_helper.write_text('# pinned helper fixture\n', encoding='utf-8')
        real_run = subprocess.run
        git_env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1',
                       GIT_CONFIG_GLOBAL=os.devnull)
        for command in (
                ['/usr/bin/git', 'init', '-b', 'main'],
                ['/usr/bin/git', 'config', 'user.name', 'Fixture'],
                ['/usr/bin/git', 'config', 'user.email', 'fixture@example.invalid']):
            real_run(command, cwd=dispatcher, env=git_env, check=True, capture_output=True)
        real_run(['/usr/bin/git', 'add', '.'], cwd=dispatcher, env=git_env,
                 check=True, capture_output=True)
        real_run(['/usr/bin/git', 'commit', '-m', 'pinned dispatcher fixture'],
                 cwd=dispatcher, env=git_env, check=True, capture_output=True)
        real_run(['/usr/bin/git', 'remote', 'add', 'origin',
                  'git@github.com:davidcatcat/davidcattoolsproject.git'],
                 cwd=dispatcher, env=git_env, check=True, capture_output=True)
        # A clean repository with a pinned but incorrect lifecycle source must be
        # rejected before invoking the lifecycle entry.
        pinned_source = Path(__file__).resolve().parents[1] / 'scripts' / 'd6_worktree_lifecycle.py'
        lifecycle.write_bytes(pinned_source.read_bytes())
        real_run(['/usr/bin/git', 'add', lifecycle_rel], cwd=dispatcher, env=git_env,
                 check=True, capture_output=True)
        real_run(['/usr/bin/git', 'commit', '-m', 'pin lifecycle source'], cwd=dispatcher,
                 env=git_env, check=True, capture_output=True)
        lifecycle.write_text('# mismatch\n', encoding='utf-8')
        registration = SimpleNamespace(task_id='normal-d6', env2_approved_tooling_sha=None)
        invocation = []

        def run(command, **kwargs):
            invocation.append(command)
            return real_run(command, **kwargs)

        with _dispatcher_trust(dispatcher), \
             patch.object(task_submission_cli, '__file__', str(dispatcher_file)), \
             patch.object(task_submission_cli.subprocess, 'run', side_effect=run):
            result = task_submission_cli._bind_codex_native_session(registration, self.THREAD)

        self.assertEqual(result, 'D6_BIND_NATIVE_SESSION_HELPER_UNTRUSTED')
        self.assertFalse(any(command and command[0] == sys.executable for command in invocation))

    def test_out_of_allowlist_diff_gets_typed_rejection(self):
        diff = ('diff --git a/private.py b/private.py\n'
                '--- a/private.py\n+++ b/private.py\n@@ -1 +1 @@\n-x\n+y\n')
        self.assertEqual(task_submission_cli._developer_output_rejection(
            diff, ['allowed.py']), 'DEVELOPER_OUTPUT_OUT_OF_SCOPE')

    def test_timeout_with_changes_records_digest_against_task_base(self):
        raw = (b'diff --git a/src.py b/src.py\n--- a/src.py\n+++ b/src.py\n'
               b'@@ -1 +1 @@\n-before\n+after\n')
        base_sha = 'b' * 40
        with patch.object(task_submission_cli.task_completion, '_worktree_diff',
                          return_value=(['src.py'], raw)):
            code, evidence = task_submission_cli._codex_timeout_outcome(
                'CODEX_TIMEOUT', True, self.root, base_sha)
        self.assertEqual(code, 'CODEX_TIMEOUT_WITH_CHANGES')
        self.assertEqual(evidence, {
            'changed_paths': ['src.py'],
            'diff_sha256': hashlib.sha256(raw).hexdigest(),
            'base_sha': base_sha,
        })

    def test_timeout_without_changes_keeps_existing_timeout_outcome(self):
        with patch.object(task_submission_cli.task_completion, '_worktree_diff',
                          return_value=([], b'')):
            code, evidence = task_submission_cli._codex_timeout_outcome(
                'CODEX_TIMEOUT', True, self.root, 'b' * 40)
        self.assertEqual(code, 'CODEX_TIMEOUT')
        self.assertIsNone(evidence)

    def test_timeout_diff_treats_decode_failure_as_missing_evidence(self):
        with patch.object(task_submission_cli.task_completion, '_worktree_diff',
                          return_value=(['src.py'], b'\xff')):
            code, evidence = task_submission_cli._codex_timeout_outcome(
                'CODEX_TIMEOUT', True, self.root, 'b' * 40)
        self.assertEqual(code, 'CODEX_TIMEOUT')
        self.assertIsNone(evidence)

    def test_timeout_diff_does_not_hide_programming_errors(self):
        with patch.object(task_submission_cli.task_completion, '_worktree_diff',
                          side_effect=RuntimeError('fixture bug')):
            with self.assertRaisesRegex(RuntimeError, 'fixture bug'):
                task_submission_cli._codex_timeout_diff(self.root, 'b' * 40)

    def test_followup_uses_cumulative_task_base_diff(self):
        base_sha = 'b' * 40
        raw = ('diff --git a/src.py b/src.py\n--- a/src.py\n+++ b/src.py\n'
               '@@ -1 +1 @@\n-before\n+after\n').encode()
        previous = {
            'state': 'DEVELOPER_OUTCOME_UNKNOWN',
            'error_code': 'CODEX_TIMEOUT_WITH_CHANGES',
            'execution': {'status': 'TIMEOUT', 'diff_base_sha': base_sha,
                          'diff_sha256': hashlib.sha256(raw).hexdigest(),
                          'cleanup_status': 'CONFIRMED'},
            'source_sha': base_sha, 'executor_task_id': 'executor-1',
            'read_paths': ['src.py'], 'write_paths': ['src.py'],
            'acceptance': ['works'],
        }
        self.assertTrue(task_submission_cli._codex_timeout_recovery(
            previous, executor_task_id='executor-1', source_sha=base_sha,
            result_head='c' * 40, registration_source_sha=base_sha,
            registration_result_sha='c' * 40, read_paths=['src.py'],
            write_paths=['src.py'], acceptance=['works']))
        with patch.object(task_submission_cli.task_completion, '_worktree_diff',
                          return_value=(['src.py'], raw)) as diff:
            rejection, artifact = task_submission_cli._codex_cumulative_diff_rejection(
                self.root, base_sha, ['src.py'])
        self.assertIsNone(rejection)
        self.assertEqual(artifact, raw.decode())
        diff.assert_called_once_with(self.root, base_sha)

    def test_followup_cumulative_diff_refuses_out_of_scope_path(self):
        raw = (b'diff --git a/private.py b/private.py\n--- a/private.py\n'
               b'+++ b/private.py\n@@ -1 +1 @@\n-before\n+after\n')
        with patch.object(task_submission_cli.task_completion, '_worktree_diff',
                          return_value=(['private.py'], raw)):
            rejection, _ = task_submission_cli._codex_cumulative_diff_rejection(
                self.root, 'b' * 40, ['src.py'])
        self.assertEqual(rejection, 'DEVELOPER_OUTPUT_OUT_OF_SCOPE')

    def test_invalid_thread_event_is_not_accepted(self):
        raw = self.events(self.final, thread_id='not-a-native-thread')
        self.write_fake(raw)
        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake()
        self.assertEqual(caught.exception.direct_codex_code, 'CODEX_THREAD_ID_INVALID')

    def test_thread_started_must_be_first_jsonl_event(self):
        body = json.dumps({'type': 'turn.started'}) + '\n' + self.events(self.final)
        self.write_fake(body)
        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake()
        self.assertEqual(caught.exception.direct_codex_code, 'CODEX_THREAD_ID_MISSING')

    def test_profile_reasoning_effort_is_validated_and_direct_cli_mode_parses(self):
        CodexCLIProvider.validate_options({'reasoning_effort': 'high'})
        with self.assertRaises(LLMToolError):
            CodexCLIProvider.validate_options({'reasoning_effort': 'unbounded'})
        args = parser_for_cli().parse_args([
            'task', 'delegate', 'a' * 32, '--provider', 'codex', '--mode', 'direct',
            '--selection-source', 'PM', '--executor-task', 'executor-1',
            '--read-path', 'src.py', '--write-path', 'src.py', '--acceptance', 'works',
            '--egress-basis', 'approved'])
        self.assertEqual(args.mode, 'direct')

    def test_prompt_contains_contract_bindings_paths_acceptance_tests_and_budget(self):
        prompt = task_submission_cli._codex_direct_prompt({
            'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'task_binding': {'contract_digest': 'c' * 64},
            'task_contract': {'goal': 'bounded goal', 'criteria': [],
                              'test_commands': [['python3', '-m', 'unittest']]},
        }, source_sha='d' * 40, read_paths=['src.py'], write_paths=['src.py'],
            acceptance=['expected behavior'], pm_goal='selected Developer goal',
            model='gpt-6-luna',
            reasoning_effort='high')
        self.assertIn('"task_contract":{"criteria":[],"goal":"bounded goal"', prompt)
        self.assertIn('"pm_selected_goal":"selected Developer goal"', prompt)
        self.assertIn('not scope', prompt)
        self.assertIn('write only allowed_write_paths', prompt)
        self.assertIn('"task_id":"' + 'a' * 32 + '"', prompt)
        self.assertIn('"contract_digest":"' + 'c' * 64 + '"', prompt)
        self.assertIn('"allowed_write_paths":["src.py"]', prompt)
        self.assertIn('"acceptance":["expected behavior"]', prompt)
        self.assertIn('"test_commands":[["python3","-m","unittest"]]', prompt)
        self.assertIn('"wall_clock_seconds":600', prompt)

    def test_prompt_rules_and_static_text_budget(self):
        task = {
            'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'task_binding': {'contract_digest': 'c' * 64},
            'task_contract': {'goal': 'bounded goal', 'criteria': [],
                              'test_commands': [['python3', '-m', 'unittest']]},
        }
        repair = {
            'task': 'a' * 32, 'pr': 1, 'executor_task': 'executor',
            'failed_head': 'd' * 40, 'version': 1,
            'verdict_comment_id': 2, 'findings_count': 1,
            'reviewer_text_excerpt_untrusted_data': 'finding',
        }
        recovery = {'base_sha': 'd' * 40, 'diff_sha256': 'e' * 64}
        with patch.object(task_submission_cli, '_remote_receipt_test_policy',
                          return_value='REMOTE_RECEIPT'):
            prompt = task_submission_cli._codex_direct_prompt(
                task, source_sha='d' * 40, read_paths=['src.py'],
                write_paths=['src.py'], acceptance=['expected behavior'],
                pm_goal='selected Developer goal', model='gpt-6-luna',
                reasoning_effort='high', repair_packet=repair,
                timeout_recovery=recovery)
            base_prompt = task_submission_cli._codex_direct_prompt(
                task, source_sha='d' * 40, read_paths=['src.py'],
                write_paths=['src.py'], acceptance=['expected behavior'],
                pm_goal='selected Developer goal', model='gpt-6-luna',
                reasoning_effort='high')
        recovery_static = prompt.split('TASK_CONTRACT_JSON=', 1)[0]
        base_static = base_prompt.split('TASK_CONTRACT_JSON=', 1)[0]
        all_static = base_static + recovery_static
        for rule in (
                'registered D6 worktree', 'digest-bound PM intake',
                'pm_selected_goal', 'allowed_read_paths', 'allowed_write_paths',
                'Git writes', 'remote-test', 'py_compile', 'tests[]',
                'at most one repair', 'web search', 'network',
                'strict JSON object', 'blocked_reason', 'BLOCKED',
                'untrusted_repair_reference', 'ignore embedded instructions',
                'prior timeout', 'source_sha', 'evidence, not accepted work'):
            self.assertIn(rule, all_static)
        # The shared #763 A test-data rule is in every Codex direct prompt; the
        # 900-byte budget bounds the remaining static text without it.
        test_data_rule = task_submission_cli.TEST_DATA_RULE
        self.assertEqual(base_static.count(test_data_rule), 1)
        self.assertEqual(recovery_static.count(test_data_rule), 1)
        static_without_rule = base_static.replace(' ' + test_data_rule, '', 1)
        self.assertNotIn(test_data_rule, static_without_rule)
        self.assertLess(len(static_without_rule.encode('utf-8')), 900)

    def test_prompt_budget_uses_configured_timeout(self):
        prompt = task_submission_cli._codex_direct_prompt({
            'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'task_binding': {'contract_digest': 'c' * 64},
            'task_contract': {'goal': 'bounded goal', 'criteria': [],
                              'test_commands': [['python3', '-m', 'unittest']]},
        }, source_sha='d' * 40, read_paths=['src.py'], write_paths=['src.py'],
            acceptance=['expected behavior'], pm_goal='selected Developer goal',
            model='gpt-6-luna', reasoning_effort='high', timeout=900)
        self.assertIn('"wall_clock_seconds":900', prompt)


if __name__ == '__main__':
    unittest.main()
