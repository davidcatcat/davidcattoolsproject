"""Public ask opt-in, real POSIX persistence and secondary failure containment."""

import io
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from llmtool import cli, diagnostics as d
from llmtool.diagnostics_runtime import RunContext
from llmtool.diagnostics_settings import DiagnosticsSettings
from llmtool.diagnostics_store import DiagnosticsStore, StoredRun, StoreWarning, persistence_supported
from llmtool.diagnostics_summary import parse_run_summary
from llmtool.errors import coded_error
from llmtool.providers.base import Reply

PROMPT = 'PRIVATE_PROMPT_A11A4'
CONTENT = 'PRIVATE_RESPONSE_A11A4'
SECRET = 'sk-' + 'a' * 40
RAW = 'PRIVATE_RAW_STDERR_BODY_A11A4'
EARLY_ERRORS = (
    (['ask', 'x', '--file', 'x'],
     'provide only one of positional text, --file, or non-empty stdin'),
    (['ask', 'x', '--skill', 'invalid/skill'],
     'skill name must use 1-64 lowercase letters, digits or hyphens'),
    (['ask', 'x', '--file', 'x', '--skill', 'invalid/skill'],
     'skill name must use 1-64 lowercase letters, digits or hyphens'),
)


class PrivateValue:
    def __str__(self):
        raise AssertionError('private value stringified')

    def __repr__(self):
        raise AssertionError('private value repr used')


@unittest.skipUnless(persistence_supported(), 'requires POSIX diagnostics backend')
class PersistedAskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'user'
        self.state = self.base / 'state'
        self.config = self.home / '.config/llmtool/config.yaml'
        self.config.parent.mkdir(parents=True)
        self.set_config()
        self.runs, self.writers = [], []

    def set_config(self, persistence=None, *, model='test-model', extra=''):
        value = ('providers:\n  default:\n    type: openai-compatible\n'
                 '    base_url: http://example.invalid\n    model: ' + model + '\n'
                 'active_provider: default\n')
        if persistence is not None:
            value += 'diagnostics:\n  persist_metadata: ' + persistence + '\n'
        self.config.write_text(value + extra)

    def invoke(self, flags=(), *, fail=None, metadata=None, argv=None, source=None,
               output=None, observer_fail=False):
        out = io.StringIO() if output is None else output
        err, client = io.StringIO(), Mock()
        client.complete.return_value = Reply(CONTENT, {'model': 'test-model', 'elapsed_seconds': 0.125,
                                                     'total_tokens': 7} if metadata is None else metadata)
        client.complete.side_effect = fail
        def run(mode):
            if observer_fail:
                raise RuntimeError(RAW)
            value = RunContext(mode)
            self.runs.append(value)
            return value
        def writer(*args):
            value = StoredRun(*args)
            self.writers.append(value)
            return value
        with ExitStack() as stack:
            stack.enter_context(patch('llmtool.config.Path.home', return_value=self.home))
            stack.enter_context(patch.dict(os.environ, {'LLMTOOL_HOME': str(self.state)}, clear=True))
            stack.enter_context(patch('llmtool.cli.sys.stdin', source))
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            stack.enter_context(patch('llmtool.diagnostics_runtime.RunContext', side_effect=run))
            stack.enter_context(patch('llmtool.diagnostics_store.StoredRun', side_effect=writer))
            factory = stack.enter_context(patch('llmtool.cli.create_client', return_value=client))
            credential = stack.enter_context(patch('llmtool.cli.api_key', return_value=None))
            status = cli.main(['ask', PROMPT, *flags] if argv is None else argv)
        return status, out.getvalue(), err.getvalue(), client, factory, credential

    def record(self):
        directories = list((self.state / 'runs').iterdir())
        self.assertEqual(len(directories), 1)
        directory = directories[0]
        summary = parse_run_summary((directory / 'summary.json').read_bytes())
        raw = (directory / 'trace.jsonl').read_bytes()
        events = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual({e['run_id'] for e in events}, {directory.name})
        self.assertEqual(summary.run_id, directory.name)
        self.assertEqual(self.runs[-1].run_id, directory.name)
        self.assertEqual([e['event_id'] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual(events[0]['type'], 'run.start')
        self.assertEqual(events[-1]['type'], 'run.end')
        self.assertEqual(summary.event_count, len(events))
        self.assertEqual(summary.trace_bytes, len(raw))
        self.assertEqual(summary.status, events[-1]['data']['status'])
        self.assertEqual(summary.started_at, events[0]['timestamp'])
        self.assertEqual(summary.ended_at, events[-1]['timestamp'])
        self.assertFalse((directory / '.reservation').exists())
        self.assertFalse((directory / 'trace.partial.jsonl').exists())
        self.assert_closed()
        return summary, events, raw

    def assert_closed(self):
        for writer in self.writers:
            self.assertTrue(writer.closed)
            for fd in writer._fds:
                with self.assertRaises(OSError):
                    os.fstat(fd)

    def assert_secondary(self, result, code, *, status=0, output=CONTENT + '\n', persisted=False):
        actual, out, err, client, *_ = result
        self.assertEqual((actual, out), (status, output))
        client.complete.assert_called_once()
        warnings = [line for line in err.splitlines() if line.startswith('llmtool: code=DIAGNOSTICS_')]
        self.assertEqual(warnings, ['llmtool: code=' + code +
                                   (' diagnostics cleanup failed; saved metadata retained' if persisted else
                                    ' diagnostics persistence unavailable; task result unaffected')])
        self.assertLess(len(warnings[0].encode()), 256)
        for marker in (PROMPT, CONTENT, SECRET, RAW, str(self.state), 'Traceback'):
            self.assertNotIn(marker, err)
        self.assertIs(self.runs[-1].persisted, persisted)
        self.assert_closed()

    def test_parser_tri_state_and_mutual_exclusion(self):
        parser = cli.parser_for_cli()
        for flags, expected in (([], None), (['--save-diagnostics'], True),
                                (['--no-save-diagnostics'], False)):
            self.assertIs(parser.parse_args(['ask', 'x', *flags]).persist_metadata, expected)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            parser.parse_args(['ask', 'x', '--save-diagnostics', '--no-save-diagnostics'])
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(self.state.exists())

    def test_default_debug_and_location_only_do_not_access_store(self):
        for flags in ([], ['--verbose'], ['--debug'], ['--no-save-diagnostics']):
            with self.subTest(flags=flags), \
                    patch.object(DiagnosticsStore, 'start', side_effect=AssertionError('must stay off')), \
                    patch('llmtool.diagnostics_store.resolve_diagnostics_home', side_effect=AssertionError('no home')):
                status, out, err, client, *_ = self.invoke(flags)
            self.assertEqual((status, out), (0, CONTENT + '\n'))
            if '--verbose' in flags or '--debug' in flags:
                self.assertIn('diagnostics_persisted=false', err)
            else:
                self.assertEqual(err, '')
            client.complete.assert_called_once()
            self.assertFalse(self.state.exists())

    def test_cli_and_trusted_user_precedence(self):
        for setting, flags, saved in ((None, ['--save-diagnostics'], True),
                                     ('false', ['--save-diagnostics'], True),
                                     ('true', [], True), ('true', ['--no-save-diagnostics'], False)):
            with self.subTest(setting=setting, flags=flags):
                self.state = self.base / ('state-' + str(len(self.runs)))
                self.set_config(setting)
                before = self.config.read_bytes()
                status, out, err, client, *_ = self.invoke(flags)
                self.assertEqual((status, out, err), (0, CONTENT + '\n', ''))
                client.complete.assert_called_once()
                self.assertEqual(self.state.exists(), saved)
                self.assertEqual(self.config.read_bytes(), before)
                if saved:
                    summary, *_ = self.record()
                    self.assertEqual(summary.status, 'success')

    def test_saved_modes_preserve_output_and_allowlist_only(self):
        metadata = {'model': 'test-model', 'elapsed_seconds': 0.125, 'total_tokens': 7,
                    'prompt': PROMPT, 'response': CONTENT, 'api_key': SECRET,
                    'Authorization': SECRET, 'stderr': RAW, 'reasoning_content': RAW,
                    'raw_body': PrivateValue(), 'path': str(self.state)}
        for mode in ([], ['--verbose'], ['--debug']):
            with self.subTest(mode=mode):
                self.state = self.base / ('mode-' + str(len(mode)) + (mode[0] if mode else 'normal'))
                status, out, err, client, *_ = self.invoke(['--save-diagnostics', *mode], metadata=metadata)
                self.assertEqual((status, out), (0, CONTENT + '\n'))
                client.complete.assert_called_once()
                if mode:
                    self.assertTrue(err.startswith('model=test-model\nelapsed_seconds=0.125\ntotal_tokens=7\n'))
                    self.assertIn('diagnostics_persisted=true', err)
                    self.assertIn('run_id=' + self.runs[-1].run_id, err)
                else:
                    self.assertEqual(err, '')
                summary, events, raw = self.record()
                self.assertEqual(summary.mode.value, 'normal' if not mode else mode[0][2:])
                self.assertEqual(summary.provider_type, 'openai-compatible')
                self.assertIsNone(summary.final_error_code)
                self.assertEqual([e['type'] for e in events],
                                 ['run.start', 'provider.request', 'provider.response', 'run.end'])
                serialized = b''.join(p.read_bytes() for p in self.state.rglob('*') if p.is_file())
                for marker in (PROMPT, CONTENT, SECRET, RAW, str(self.state), 'reasoning_content'):
                    self.assertNotIn(marker.encode(), serialized)
                    self.assertNotIn(marker, err)
                self.assertIn(b'"total_tokens":7', raw)

    def test_saved_expected_and_incomplete_provider_failures(self):
        for code, status in (('PROVIDER_TIMEOUT', 'error'), ('MODEL_OUTPUT_INCOMPLETE', 'incomplete')):
            with self.subTest(code=code):
                self.state = self.base / code
                exc = coded_error('safe provider failure', code, 'provider.openai-compatible')
                result, out, err, client, *_ = self.invoke(['--save-diagnostics'], fail=exc)
                self.assertEqual((result, out), (1, ''))
                self.assertEqual(err, 'llmtool: safe provider failure\nllmtool: code=' + code +
                                 ' run_id=' + self.runs[-1].run_id + ' trace=saved\n')
                client.complete.assert_called_once()
                summary, events, *_ = self.record()
                self.assertEqual(summary.status, status)
                self.assertEqual(summary.final_error_code.value, code)
                self.assertEqual([e['type'] for e in events], ['run.start', 'provider.request', 'error', 'run.end'])

    def test_saved_input_conflict_has_no_credential_or_provider_calls(self):
        status, out, err, client, factory, credential = self.invoke(
            argv=['ask', 'x', '--file', 'x', '--save-diagnostics'])
        self.assertEqual((status, out), (1, ''))
        self.assertIn('code=USER_INPUT_ERROR', err)
        self.assertIn('trace=saved', err)
        factory.assert_not_called()
        credential.assert_not_called()
        client.complete.assert_not_called()
        summary, events, *_ = self.record()
        self.assertEqual(summary.final_error_code, d.ErrorCode.USER_INPUT_ERROR)
        self.assertEqual([e['type'] for e in events], ['run.start', 'error', 'run.end'])

    def test_legacy_input_and_skill_errors_precede_malformed_user_yaml(self):
        self.config.write_text('providers: [\n')
        for flags in ([], ['--no-save-diagnostics'], ['--save-diagnostics']):
            for argv, message in EARLY_ERRORS:
                with self.subTest(flags=flags, argv=argv), \
                        patch.object(DiagnosticsStore, 'start') as store, \
                        patch('llmtool.cli.resolve_config') as config:
                    status, out, err, client, factory, credential = self.invoke(argv=[*argv, *flags])
                self.assertEqual((status, out), (1, ''))
                self.assertEqual(err.splitlines()[0], 'llmtool: ' + message)
                trace = 'unavailable' if '--save-diagnostics' in flags else 'not-saved'
                self.assertEqual(err.splitlines()[1], 'llmtool: code=USER_INPUT_ERROR run_id=' +
                                 self.runs[-1].run_id + ' trace=' + trace)
                self.assertNotIn('CONFIG_INVALID', err)
                self.assertEqual(len(err.splitlines()), 3 if trace == 'unavailable' else 2)
                if trace == 'unavailable':
                    self.assertIn('code=DIAGNOSTICS_STORE_UNAVAILABLE', err.splitlines()[2])
                store.assert_not_called()
                config.assert_not_called()
                factory.assert_not_called()
                credential.assert_not_called()
                client.complete.assert_not_called()
                self.assertFalse(self.state.exists())

    def test_legacy_early_errors_precede_invalid_trusted_opt_in_settings(self):
        self.set_config('true', extra='  max_runs: invalid\n')
        for flags in ([], ['--no-save-diagnostics'], ['--save-diagnostics']):
            for argv, message in EARLY_ERRORS:
                with self.subTest(flags=flags, argv=argv), patch.object(DiagnosticsStore, 'start') as store:
                    status, out, err, client, factory, credential = self.invoke(argv=[*argv, *flags])
                self.assertEqual((status, out), (1, ''))
                self.assertEqual(err.splitlines()[0], 'llmtool: ' + message)
                self.assertIn('code=USER_INPUT_ERROR', err.splitlines()[1])
                self.assertNotIn('CONFIG_INVALID', err)
                store.assert_not_called()
                factory.assert_not_called()
                credential.assert_not_called()
                client.complete.assert_not_called()
                self.assertFalse(self.state.exists())

    def test_valid_settings_still_save_early_errors_with_correct_precedence(self):
        for setting, flags, saved in ((None, [], False), (None, ['--save-diagnostics'], True),
                                     ('true', [], True), ('true', ['--no-save-diagnostics'], False)):
            for argv, message in EARLY_ERRORS:
                with self.subTest(setting=setting, flags=flags, argv=argv):
                    self.state = self.base / ('early-' + str(len(self.runs)))
                    self.set_config(setting)
                    status, out, err, client, factory, credential = self.invoke(argv=[*argv, *flags])
                    self.assertEqual((status, out), (1, ''))
                    self.assertEqual(err.splitlines()[0], 'llmtool: ' + message)
                    self.assertEqual(err.splitlines()[1], 'llmtool: code=USER_INPUT_ERROR run_id=' +
                                     self.runs[-1].run_id + ' trace=' + ('saved' if saved else 'not-saved'))
                    self.assertEqual(len(err.splitlines()), 2)
                    factory.assert_not_called()
                    credential.assert_not_called()
                    client.complete.assert_not_called()
                    self.assertEqual(self.state.exists(), saved)
                    if saved:
                        summary, events, *_ = self.record()
                        self.assertEqual(summary.final_error_code, d.ErrorCode.USER_INPUT_ERROR)
                        self.assertEqual([event['type'] for event in events], ['run.start', 'error', 'run.end'])

    def test_saved_readiness_error_does_not_consume_open_stdin(self):
        self.set_config(model='null')
        source = Mock()
        source.isatty.return_value = False
        source.buffer.read.side_effect = AssertionError('must not consume stdin')
        status, out, err, client, factory, credential = self.invoke(
            argv=['ask', '--save-diagnostics'], source=source)
        self.assertEqual((status, out), (1, ''))
        self.assertIn('code=CONFIG_PROVIDER_NOT_READY', err)
        self.assertIn('trace=saved', err)
        source.buffer.read.assert_not_called()
        factory.assert_not_called()
        credential.assert_not_called()
        client.complete.assert_not_called()
        summary, *_ = self.record()
        self.assertEqual(summary.final_error_code, d.ErrorCode.CONFIG_PROVIDER_NOT_READY)

    def test_malformed_diagnostics_config_precedes_store_and_provider(self):
        self.set_config('not-a-boolean')
        with patch.object(DiagnosticsStore, 'start') as store:
            status, out, err, client, factory, credential = self.invoke(['--save-diagnostics'])
        self.assertEqual((status, out), (1, ''))
        self.assertIn('code=CONFIG_INVALID', err)
        store.assert_not_called()
        factory.assert_not_called()
        credential.assert_not_called()
        client.complete.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_project_diagnostics_cannot_enable_or_override_store(self):
        project = self.base / 'project'
        project.mkdir()
        (project / '.llmtool.yaml').write_text('diagnostics:\n  persist_metadata: true\n')
        with patch('llmtool.config.Path.cwd', return_value=project):
            status, out, err, client, factory, credential = self.invoke()
        self.assertEqual((status, out), (1, ''))
        self.assertIn('code=CONFIG_INVALID', err)
        client.complete.assert_not_called()
        factory.assert_not_called()
        credential.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_create_failures_and_unsupported_backend_continue_once_unsaved(self):
        for target in ('llmtool.diagnostics_store.DiagnosticsStore',
                       'llmtool.diagnostics_store.DiagnosticsStore.start',
                       'llmtool.diagnostics_store.resolve_diagnostics_home'):
            with self.subTest(target=target), patch(target, side_effect=RuntimeError(RAW)):
                self.assert_secondary(self.invoke(['--save-diagnostics', '--debug']),
                                      'DIAGNOSTICS_STORE_UNAVAILABLE')
            self.assertFalse(self.state.exists())
        def unsupported(settings):
            return DiagnosticsStore(settings, platform='win32')
        with patch('llmtool.diagnostics_store.DiagnosticsStore', side_effect=unsupported):
            self.assert_secondary(self.invoke(['--save-diagnostics']), 'DIAGNOSTICS_STORE_UNAVAILABLE')
        self.assertFalse(self.state.exists())

    def test_unsafe_store_rejected_without_chmod_or_target_mutation(self):
        self.state.mkdir(mode=0o755)
        self.state.chmod(0o755)  # Cloud may inherit a private umask.
        before = self.state.stat().st_mode
        self.assert_secondary(self.invoke(['--save-diagnostics']), 'DIAGNOSTICS_STORE_UNSAFE')
        self.assertEqual(self.state.stat().st_mode, before)
        self.assertEqual(list(self.state.iterdir()), [])
        self.state.rmdir()
        target = self.base / 'target'
        target.mkdir(mode=0o700)
        self.state.symlink_to(target, target_is_directory=True)
        self.assert_secondary(self.invoke(['--save-diagnostics']), 'DIAGNOSTICS_STORE_UNSAFE')
        self.assertEqual(list(target.iterdir()), [])

    def test_store_warning_payload_is_never_copied_or_stringified(self):
        for warning in (StoreWarning(d.ErrorCode.DIAGNOSTICS_STORE_UNAVAILABLE, RAW + SECRET),
                        PrivateValue()):
            def start(store, identity):
                store.warning = warning
                return None
            with self.subTest(warning_type=type(warning)), patch.object(DiagnosticsStore, 'start', new=start):
                self.assert_secondary(self.invoke(['--save-diagnostics']), 'DIAGNOSTICS_STORE_UNAVAILABLE')

    def test_append_false_exception_write_and_fsync_failures_are_secondary(self):
        cases = [('llmtool.diagnostics_store.StoredRun.append', {'return_value': False}),
                 ('llmtool.diagnostics_store.StoredRun.append', {'side_effect': RuntimeError(RAW)}),
                 ('llmtool.diagnostics_store._write', {'side_effect': OSError(RAW)}),
                 ('llmtool.diagnostics_store.os.fsync', {'side_effect': OSError(RAW)})]
        for target, effect in cases:
            with self.subTest(target=target, effect=list(effect)):
                self.state = self.base / ('failure-' + str(len(self.runs)))
                with patch(target, **effect):
                    expected = ('DIAGNOSTICS_WRITE_FAILED' if 'StoredRun' in target else
                                'DIAGNOSTICS_STORE_UNAVAILABLE')
                    self.assert_secondary(self.invoke(['--save-diagnostics', '--debug']), expected)

    def test_late_append_failure_stops_writes_and_releases_all_descriptors(self):
        original = StoredRun.append
        def append(writer, event):
            if event.type is d.EventType.PROVIDER_RESPONSE:
                raise OSError(RAW)
            return original(writer, event)
        with patch.object(StoredRun, 'append', new=append):
            self.assert_secondary(self.invoke(['--save-diagnostics']), 'DIAGNOSTICS_WRITE_FAILED')
        self.assertEqual([e.type.value for e in self.writers[-1].events], ['run.start', 'provider.request'])
        self.assertTrue((self.state / 'runs' / self.runs[-1].run_id / 'trace.partial.jsonl').exists())

    def test_completion_exception_false_and_publication_failure_are_secondary(self):
        for target, effect in (('llmtool.diagnostics_store.StoredRun.finish', {'return_value': False}),
                               ('llmtool.diagnostics_store.StoredRun.finish', {'side_effect': OSError(RAW)}),
                               ('llmtool.diagnostics_store._publish', {'side_effect': OSError(RAW)})):
            with self.subTest(target=target, effect=list(effect)):
                self.state = self.base / ('finish-' + str(len(self.runs)))
                with patch(target, **effect):
                    self.assert_secondary(self.invoke(['--save-diagnostics', '--verbose']), 'DIAGNOSTICS_WRITE_FAILED')

    def test_postpublication_reservation_failure_still_reports_saved(self):
        from llmtool.diagnostics_reader import DiagnosticsReader
        original_unlink = os.unlink
        def unlink(name, *args, **kwargs):
            if name == '.reservation':
                raise PermissionError(RAW)
            return original_unlink(name, *args, **kwargs)
        for failed in (False, True):
            with self.subTest(provider_failed=failed):
                self.state = self.base / ('committed-' + str(failed))
                error = coded_error('provider failed', d.ErrorCode.PROVIDER_TIMEOUT, 'provider') if failed else None
                with patch('llmtool.diagnostics_store.os.unlink', side_effect=unlink):
                    status, out, err, client, *_ = self.invoke(['--save-diagnostics', '--verbose'], fail=error)
                self.assertEqual((status, out), (1, '') if failed else (0, CONTENT + '\n'))
                client.complete.assert_called_once()
                self.assertTrue(self.runs[-1].persisted)
                self.assertIn('diagnostics_persisted=true', err)
                if failed:
                    self.assertIn('trace=saved', err)
                self.assertNotIn('code=DIAGNOSTICS_', err)
                self.assertNotIn(RAW, err)
                directory = self.state / 'runs' / self.runs[-1].run_id
                self.assertTrue((directory / '.reservation').exists())
                self.assertEqual(DiagnosticsReader(home=self.state).trace(directory.name)['state'], 'complete')
                self.assert_closed()

    def test_close_error_still_releases_remaining_descriptors_and_preserves_result(self):
        original_close, original_finish = os.close, StoredRun.finish
        released = []
        def finish(writer, summary):
            failed_fd = writer.trace
            def close(fd):
                original_close(fd)
                released.append(fd)
                if fd == failed_fd:
                    raise OSError(RAW)
            with patch('llmtool.diagnostics_store.os.close', side_effect=close):
                return original_finish(writer, summary)
        with patch.object(StoredRun, 'finish', new=finish):
            self.assert_secondary(self.invoke(['--save-diagnostics']), 'DIAGNOSTICS_WRITE_FAILED', persisted=True)
        self.assertTrue(set(self.writers[-1]._fds) <= set(released))
        from llmtool.diagnostics_reader import DiagnosticsReader
        self.assertEqual(DiagnosticsReader(home=self.state).trace(self.runs[-1].run_id)['state'], 'complete')

    def test_start_cleanup_close_error_still_releases_remaining_descriptors(self):
        from llmtool import diagnostics_store
        original_file, original_close = diagnostics_store._file, os.close
        acquired, released = [], []
        def file(*args, **kwargs):
            fd = original_file(*args, **kwargs)
            acquired.append(fd)
            return fd
        def close(fd):
            original_close(fd)
            released.append(fd)
            if fd == acquired[-1]:
                raise OSError(RAW)
        with patch('llmtool.diagnostics_store._file', side_effect=file), \
                patch('llmtool.diagnostics_store.os.close', side_effect=close), \
                patch.object(DiagnosticsStore, '_capacity', side_effect=OSError(RAW)):
            result = self.invoke(['--save-diagnostics'])
        self.assert_secondary(result, 'DIAGNOSTICS_STORE_UNAVAILABLE')
        self.assertTrue(set(acquired) <= set(released))
        for fd in set(acquired):
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_provider_failure_precedes_one_secondary_store_warning(self):
        exc = coded_error('safe provider failure', 'PROVIDER_TIMEOUT', 'provider.openai-compatible')
        for target in ('llmtool.diagnostics_store.DiagnosticsStore.start',
                       'llmtool.diagnostics_store.StoredRun.append',
                       'llmtool.diagnostics_store.StoredRun.finish'):
            with self.subTest(target=target), patch(target, side_effect=OSError(RAW)):
                result = self.invoke(['--save-diagnostics', '--debug'], fail=exc)
            expected = 'DIAGNOSTICS_STORE_UNAVAILABLE' if target.endswith('start') else 'DIAGNOSTICS_WRITE_FAILED'
            self.assert_secondary(result, expected, status=1, output='')
            self.assertEqual(result[2].splitlines()[0], 'llmtool: safe provider failure')
            self.assertEqual(result[2].splitlines()[1], 'llmtool: code=PROVIDER_TIMEOUT run_id=' +
                             self.runs[-1].run_id + ' trace=unavailable')
            self.assertTrue(result[2].splitlines()[2].startswith('llmtool: code=DIAGNOSTICS_'))

    def test_start_failure_then_readiness_error_does_not_consume_stdin(self):
        self.set_config(model='null')
        source = Mock()
        source.isatty.return_value = False
        source.buffer.read.side_effect = AssertionError('no input read')
        with patch.object(DiagnosticsStore, 'start', side_effect=OSError(RAW)):
            status, out, err, client, factory, credential = self.invoke(
                argv=['ask', '--save-diagnostics'], source=source)
        self.assertEqual((status, out), (1, ''))
        self.assertIn('code=CONFIG_PROVIDER_NOT_READY', err.splitlines()[1])
        self.assertIn('code=DIAGNOSTICS_STORE_UNAVAILABLE', err.splitlines()[2])
        source.buffer.read.assert_not_called()
        client.complete.assert_not_called()
        factory.assert_not_called()
        credential.assert_not_called()

    def test_collision_rebinds_buffered_events_summary_and_display(self):
        first_id, next_id = 'a' * 32, 'b' * 32
        existing = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.state).start(first_id)
        self.assertIsNotNone(existing)
        self.addCleanup(existing.close)
        with patch.object(d, 'new_run_id', side_effect=[first_id, next_id, 'c' * 32]):
            status, out, err, client, *_ = self.invoke(['--save-diagnostics', '--debug'])
        self.assertEqual((status, out), (0, CONTENT + '\n'))
        self.assertIn('run_id=' + next_id, err)
        self.assertNotIn(first_id, err)
        client.complete.assert_called_once()
        directory = self.state / 'runs' / next_id
        summary = parse_run_summary((directory / 'summary.json').read_bytes())
        events = [json.loads(line) for line in (directory / 'trace.jsonl').read_bytes().splitlines()]
        self.assertEqual(summary.run_id, next_id)
        self.assertEqual(self.runs[-1].run_id, next_id)
        self.assertEqual({e['run_id'] for e in events}, {next_id})
        self.assertEqual([e['event_id'] for e in events], [1, 2, 3, 4])
        self.assertTrue((self.state / 'runs' / first_id / '.reservation').exists())
        self.assert_closed()

    def test_three_collisions_fail_safely_and_keep_ephemeral_id(self):
        identity = 'a' * 32
        existing = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.state).start(identity)
        self.assertIsNotNone(existing)
        self.addCleanup(existing.close)
        with patch.object(d, 'new_run_id', return_value=identity) as entropy:
            result = self.invoke(['--save-diagnostics', '--debug'])
        self.assert_secondary(result, 'DIAGNOSTICS_RUN_COLLISION')
        self.assertEqual(entropy.call_count, 3)  # ephemeral ID + two collision regenerations
        self.assertEqual(self.runs[-1].run_id, identity)

    def test_capacity_failure_continues_once_without_deleting_active_run(self):
        existing = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True, max_runs=1), home=self.state).start()
        self.assertIsNotNone(existing)
        self.addCleanup(existing.close)
        self.set_config('true', extra='  max_runs: 1\n')
        self.assert_secondary(self.invoke(), 'DIAGNOSTICS_STORE_FULL')
        self.assertTrue((self.state / 'runs' / existing.run_id / '.reservation').exists())

    def test_saved_interrupt_and_output_closed_keep_original_exits(self):
        result, out, err, client, *_ = self.invoke(['--save-diagnostics'], fail=KeyboardInterrupt())
        self.assertEqual((result, out, err), (130, '', 'llmtool: interrupted\n'))
        client.complete.assert_called_once()
        summary, *_ = self.record()
        self.assertEqual(summary.status, 'interrupted')
        self.state = self.base / 'output-closed'
        class ClosedPipe(io.StringIO):
            def write(self, value):
                raise BrokenPipeError
        result, out, err, client, *_ = self.invoke(['--save-diagnostics'], output=ClosedPipe())
        self.assertEqual((result, out, err), (0, '', ''))
        client.complete.assert_called_once()
        summary, *_ = self.record()
        self.assertEqual(summary.status, 'output_closed')

    def test_interrupt_and_output_closed_survive_finish_failure(self):
        class ClosedPipe(io.StringIO):
            def write(self, value):
                raise BrokenPipeError
        for kwargs, status, first in (({'fail': KeyboardInterrupt()}, 130, 'llmtool: interrupted\n'),
                                     ({'output': ClosedPipe()}, 0, '')):
            with self.subTest(status=status), patch.object(StoredRun, 'finish', side_effect=OSError(RAW)):
                result = self.invoke(['--save-diagnostics'], **kwargs)
            self.assert_secondary(result, 'DIAGNOSTICS_WRITE_FAILED', status=status, output='')
            self.assertTrue(result[2].startswith(first))

    def test_unexpected_provider_exception_not_stringified_or_saved(self):
        class PrivateException(Exception):
            def __str__(self):
                raise AssertionError('private exception stringified')
        status, out, err, client, *_ = self.invoke(['--save-diagnostics'], fail=PrivateException(RAW))
        self.assertEqual((status, out), (1, ''))
        self.assertTrue(err.startswith('llmtool: internal error; details omitted\n'))
        client.complete.assert_called_once()
        summary, _, raw = self.record()
        self.assertEqual(summary.final_error_code, d.ErrorCode.INTERNAL_UNEXPECTED)
        self.assertNotIn(RAW.encode(), raw)
        self.assertNotIn(RAW, err)

    def test_invalid_metadata_drop_is_counted_and_safe_trace_can_finish(self):
        status, out, err, client, *_ = self.invoke(['--save-diagnostics', '--debug'], metadata={'model': SECRET})
        self.assertEqual((status, out), (0, CONTENT + '\n'))
        client.complete.assert_called_once()
        summary, events, raw = self.record()
        self.assertEqual(summary.dropped_event_count, 1)
        self.assertNotIn(SECRET.encode(), raw)
        self.assertNotIn(SECRET, err)
        self.assertEqual([e['type'] for e in events], ['run.start', 'provider.request', 'warning', 'run.end'])

    def test_event_construction_failure_cannot_claim_saved_or_fail_task(self):
        with patch.object(d, 'DiagnosticEvent', side_effect=ValueError(RAW)):
            result = self.invoke(['--save-diagnostics', '--debug'])
        self.assert_secondary(result, 'DIAGNOSTICS_WRITE_FAILED')
        self.assertGreater(self.runs[-1].dropped_event_count, 0)

    def test_observer_initialization_failure_warns_only_for_explicit_opt_in(self):
        result = self.invoke(['--save-diagnostics'], observer_fail=True)
        self.assertEqual(result[:2], (0, CONTENT + '\n'))
        self.assertEqual(result[2].count('code=DIAGNOSTICS_STORE_UNAVAILABLE'), 1)
        self.assertNotIn(RAW, result[2])
        result[3].complete.assert_called_once()
        self.assertFalse(self.state.exists())
        result = self.invoke(observer_fail=True)
        self.assertEqual(result[:3], (0, CONTENT + '\n', ''))


if __name__ == '__main__':
    unittest.main()
