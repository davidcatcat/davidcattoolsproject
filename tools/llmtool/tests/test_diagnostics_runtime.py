"""Offline ask lifecycle, containment, privacy and coded transport evidence."""

import ast
import copy
import io
import json
import os
import socket
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from llmtool import cli, diagnostics as d
from llmtool.config import DEFAULTS, api_key
from llmtool.diagnostics_runtime import END_SEVERITY, RunContext
from llmtool.errors import LLMToolError, coded_error
from llmtool.providers.base import Reply
from llmtool.providers.openai import OpenAIProvider
from llmtool.providers.openai_compatible import OpenAICompatibleClient

PROMPT = 'PRIVATE_PROMPT_A11A2'
CONTENT = 'PRIVATE_RESPONSE_A11A2'
MARKER = 'SUPER_SECRET_A11A2_MARKER'


def configured():
    value = copy.deepcopy(DEFAULTS)
    value['providers']['default'].update(base_url='http://example.invalid', model='qwen/test')
    return value


class PrivateObject:
    def __str__(self):
        raise AssertionError('must never stringify private metadata')

    def __repr__(self):
        raise AssertionError('must never repr private metadata')


class LifecycleTests(unittest.TestCase):
    def test_success_sequence_metadata_and_no_task_storage(self):
        run = RunContext()
        run.request(configured())
        run.response({'elapsed_seconds': 0.25, 'model': 'qwen/test', 'total_tokens': 0})
        run.finish('success')
        self.assertEqual([e.type.value for e in run.events],
                         ['run.start', 'provider.request', 'provider.response', 'run.end'])
        self.assertEqual([e.event_id for e in run.events], [1, 2, 3, 4])
        self.assertRegex(run.run_id, r'^[0-9a-f]{32}$')
        self.assertEqual(run.events[2].data['duration_ms'], 250)
        self.assertTrue(run.events[2].data['token_usage_available'])
        self.assertIsNone(run.events[0].span_id)
        self.assertEqual(run.final_status, 'success')
        self.assertEqual(run.dropped_event_count, 0)
        self.assertFalse({'prompt', 'response', 'cwd', 'config', 'api_key'} & vars(run).keys())

    def test_final_status_severity_table_and_finish_idempotence(self):
        for status, severity in END_SEVERITY.items():
            with self.subTest(status=status):
                run = RunContext()
                run.finish(status)
                run.finish('error')
                self.assertEqual(run.events[-1].type, d.EventType.RUN_END)
                self.assertEqual(run.events[-1].severity.value, severity)
                self.assertEqual(run.events[-1].data['status'], status)
                self.assertEqual(len(run.events), 2)

    def test_duration_uses_monotonic_and_bounds(self):
        with patch('llmtool.diagnostics_runtime.time.monotonic', side_effect=[10, 10.125]):
            run = RunContext()
            run.finish('success')
        self.assertEqual(run.events[-1].data['duration_ms'], 125)
        for ending, expected in ((-1, 0), (10**20, d.MAX_DURATION_MS)):
            with patch('llmtool.diagnostics_runtime.time.monotonic', side_effect=[0, ending]):
                run = RunContext()
                run.finish('success')
            self.assertEqual(run.events[-1].data['duration_ms'], expected)

    def test_drop_count_warning_once_and_no_sequence_gaps(self):
        run = RunContext()
        for _ in range(3):
            run.emit(d.EventType.ERROR, 'input', d.DiagnosticSeverity.INFO, {})
        run.finish('success')
        self.assertEqual(run.dropped_event_count, 3)
        self.assertEqual([e.event_id for e in run.events], [1, 2, 3])
        warnings = [e for e in run.events if e.type == d.EventType.WARNING]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].data['code'], 'DIAGNOSTICS_EVENT_DROPPED')
        self.assertEqual(run.events[-1].type, d.EventType.RUN_END)

    def test_all_event_construction_fails_without_recursion(self):
        with patch.object(d, 'DiagnosticEvent', side_effect=ValueError(MARKER)) as construct:
            run = RunContext()
            run.request(configured())
            run.response({'model': 'test'})
            run.finish('success')
        self.assertEqual(run.events, ())
        self.assertEqual(run.dropped_event_count, 5)  # four events + failed one-time warning
        self.assertEqual(construct.call_count, 5)
        self.assertTrue(run.emitted_drop_warning)
        self.assertEqual(run.final_status, 'success')

    def test_memory_budgets_drop_without_unbounded_accumulation(self):
        for name, limit in (('MAX_RUN_EVENTS', 2), ('MAX_RUN_EVENT_BYTES', 600)):
            with self.subTest(name=name), patch.object(d, name, limit):
                run = RunContext()
                for _ in range(100):
                    run.request(configured())
                self.assertLessEqual(len(run.events), 2)
                self.assertLessEqual(run._event_bytes, d.MAX_RUN_EVENT_BYTES)
                self.assertGreater(run.dropped_event_count, 90)
                self.assertLessEqual(sum(e.type == d.EventType.WARNING for e in run.events), 1)

    def test_entropy_failure_does_not_invent_an_identifier(self):
        with patch.object(d, 'new_run_id', side_effect=OSError(MARKER)):
            run = RunContext()
            run.finish('success')
        self.assertIsNone(run.run_id)
        self.assertEqual(run.final_status, 'success')
        self.assertIn('run_id=unavailable', run.failure_line(d.ErrorCode.INTERNAL_UNEXPECTED))

    def test_adapter_unknown_values_never_stringified(self):
        run = RunContext(d.DiagnosticMode.DEBUG)
        run.request(configured())
        run.response({'model': 'claude-3-5-sonnet@20240620', 'stderr': PrivateObject(),
                      'body': PrivateObject(), 'reasoning': MARKER})
        run.finish('success')
        stream = io.StringIO()
        run.display(stream)
        self.assertNotIn(MARKER, stream.getvalue())
        self.assertNotIn('stderr', stream.getvalue())
        self.assertEqual(run.dropped_event_count, 0)

    def test_invalid_allowlisted_metadata_drops_event(self):
        for metadata in ({'model': 'not a model'}, {'model': 'm' * 129},
                         {'model': 'sk-' + 'a' * 32}, {'elapsed_seconds': float('nan')},
                         {'elapsed_seconds': -1}, {'total_tokens': True}, {'total_tokens': -1},
                         {'model': PrivateObject()}, PrivateObject()):
            with self.subTest(type=type(metadata)):
                run = RunContext()
                self.assertEqual(run.response(metadata), {})
                self.assertGreater(run.dropped_event_count, 0)
                self.assertFalse(any(e.type == d.EventType.PROVIDER_RESPONSE for e in run.events))

    def test_unavailable_tokens_not_estimated_and_redaction_marker_kept(self):
        run = RunContext(d.DiagnosticMode.VERBOSE)
        run.response({'model': '[redacted]', 'input_tokens': None})
        event = next(e for e in run.events if e.type == d.EventType.PROVIDER_RESPONSE)
        self.assertIsNone(event.data['input_tokens'])
        self.assertFalse(event.data['token_usage_available'])
        self.assertNotIn('model', event.data)
        stream = io.StringIO()
        run.display(stream)
        self.assertIn('model=[redacted]', stream.getvalue())

    def test_coded_error_and_legacy_stage_fallback(self):
        for exc, expected in ((coded_error('request timed out', 'PROVIDER_TIMEOUT', 'provider.openai',
                                           retryable=True), d.ErrorCode.PROVIDER_TIMEOUT),
                              (LLMToolError('safe legacy message'), d.ErrorCode.CONFIG_INVALID)):
            run = RunContext()
            self.assertEqual(run.error(exc, d.ErrorCode.CONFIG_INVALID, 'config'), expected)
            self.assertEqual(run.events[-1].data['code'], expected.value)
            self.assertFalse(run.events[-1].data['fallback_allowed'])

    def test_error_annotation_failure_preserves_primary_error(self):
        with patch.object(d, 'DiagnosticError', side_effect=ValueError(MARKER)):
            exc = coded_error('existing safe message', 'PROVIDER_TIMEOUT', 'provider.openai')
        self.assertEqual(str(exc), 'existing safe message')
        self.assertEqual(exc.args, ('existing safe message',))
        self.assertTrue(exc.diagnostic_dropped)
        run = RunContext()
        self.assertEqual(run.error(exc, d.ErrorCode.PROVIDER_RUNTIME_ERROR, 'provider'),
                         d.ErrorCode.PROVIDER_TIMEOUT)
        self.assertEqual(run.dropped_event_count, 1)

    def test_display_failure_is_secondary(self):
        run = RunContext(d.DiagnosticMode.DEBUG)
        run.finish('success')
        run.display(Mock(write=Mock(side_effect=OSError(MARKER))))
        self.assertEqual(run.final_status, 'success')
        self.assertGreater(run.dropped_event_count, 0)
        self.assertEqual(run.events[-1].type, d.EventType.RUN_END)


class AskTests(unittest.TestCase):
    def invoke(self, argv=None, *, metadata=None, fail=None, configure_fail=None, input_stream=None,
               reply=None):
        out, err = io.StringIO(), io.StringIO()
        client = Mock()
        client.complete.return_value = reply if reply is not None else Reply(
            CONTENT, {'elapsed_seconds': 0.1, 'model': 'qwen/test',
                      'total_tokens': 7} if metadata is None else metadata)
        client.complete.side_effect = fail
        runs = []
        def new_run(mode):
            value = RunContext(mode)
            runs.append(value)
            return value
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            stack.enter_context(patch('llmtool.cli.sys.stdin', input_stream))
            resolve = stack.enter_context(patch('llmtool.cli.resolve_config', return_value=configured(),
                                               side_effect=configure_fail))
            credential = stack.enter_context(patch('llmtool.cli.api_key', return_value=None))
            factory = stack.enter_context(patch('llmtool.cli.create_client', return_value=client))
            stack.enter_context(patch('llmtool.diagnostics_runtime.RunContext', side_effect=new_run))
            status = cli.main(['ask', PROMPT] if argv is None else argv)
        return status, out.getvalue(), err.getvalue(), client, runs, resolve, credential, factory

    def test_normal_success_byte_compatible_and_quiet(self):
        status, out, err, client, runs, *_ = self.invoke()
        self.assertEqual((status, out, err), (0, CONTENT + '\n', ''))
        client.complete.assert_called_once()
        self.assertEqual(runs[0].final_status, 'success')

    def test_conflicting_or_non_ask_persistence_flags_rejected_before_observation(self):
        for argv in (['ask', 'x', '--save-diagnostics', '--no-save-diagnostics'],
                     ['review-diff', 'x', '--save-diagnostics'],
                     ['context-pack', 'x', '--no-save-diagnostics']):
            out, err = io.StringIO(), io.StringIO()
            with self.subTest(argv=argv), redirect_stdout(out), redirect_stderr(err), \
                    patch('llmtool.cli.create_client') as factory, \
                    patch('llmtool.diagnostics_runtime.RunContext') as run, \
                    patch('llmtool.diagnostics_store.DiagnosticsStore.start') as store, \
                    patch('os.mkdir') as mkdir, \
                    self.assertRaises(SystemExit) as caught:
                cli.main(argv)
            self.assertEqual(caught.exception.code, 2)
            factory.assert_not_called()
            run.assert_not_called()
            store.assert_not_called()
            mkdir.assert_not_called()
            self.assertEqual(out.getvalue(), '')
            self.assertEqual(err.getvalue(), 'llmtool: invalid command or options; use --help\n')

    def test_ask_help_exposes_metadata_only_persistence_flags(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            cli.main(['ask', '--help'])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn('--save-diagnostics', out.getvalue())
        self.assertIn('--no-save-diagnostics', out.getvalue())
        self.assertIn('metadata only', out.getvalue())

    def test_verbose_preserves_metadata_order_and_appends_safe_fields(self):
        status, out, err, client, *_ = self.invoke(['ask', PROMPT, '--verbose'])
        self.assertEqual(status, 0)
        self.assertEqual(out, CONTENT + '\n')
        self.assertTrue(err.startswith('elapsed_seconds=0.1\nmodel=qwen/test\ntotal_tokens=7\n'))
        self.assertRegex(err, r'run_id=[0-9a-f]{32}\nprovider_type=openai-compatible\ndiagnostics_persisted=false\n$')
        self.assertNotIn('event_id', err)
        client.complete.assert_called_once()

    def test_verbose_metadata_survives_stricter_event_model_validation(self):
        for model in ('_internal', 'models/./x'):
            for flag in ('--verbose', '--debug'):
                with self.subTest(model=model, flag=flag):
                    metadata = {'elapsed_seconds': 0.1, 'model': model,
                                'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}
                    status, out, err, client, runs, *_ = self.invoke(
                        ['ask', PROMPT, flag], metadata=metadata)
                    self.assertEqual((status, out), (0, CONTENT + '\n'))
                    self.assertTrue(err.startswith(''.join(f'{k}={v}\n' for k, v in metadata.items())))
                    self.assertEqual(runs[0].dropped_event_count, 1)
                    self.assertFalse(any(e.type == d.EventType.PROVIDER_RESPONSE for e in runs[0].events))
                    client.complete.assert_called_once()

    def test_credential_client_message_order_matches_legacy_ask(self):
        order = []
        original = cli.messages_for
        def messages(*args):
            order.append('messages')
            return original(*args)
        with patch('llmtool.cli.api_key', side_effect=lambda *_: order.append('key')), \
             patch('llmtool.cli.create_client') as factory, \
             patch('llmtool.cli.messages_for', side_effect=messages), \
             patch('llmtool.cli.resolve_config', return_value=configured()), \
             patch('llmtool.cli.sys.stdin', None), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            factory.side_effect = lambda *_: (order.append('client') or Mock(complete=Mock(return_value=Reply(CONTENT, {}))))
            self.assertEqual(cli.main(['ask', PROMPT]), 0)
        self.assertEqual(order, ['key', 'client', 'messages'])

    def test_debug_safe_summaries_and_one_request(self):
        metadata = {'model': 'test', 'reasoning': MARKER, 'stderr': MARKER,
                    'path': '/private/secret', 'raw': PrivateObject()}
        status, out, err, client, runs, *_ = self.invoke(['ask', PROMPT, '--debug'], metadata=metadata)
        self.assertEqual((status, out), (0, CONTENT + '\n'))
        for hidden in (PROMPT, CONTENT, MARKER, '/private/secret', 'reasoning'):
            self.assertNotIn(hidden, err)
            self.assertNotIn(hidden, '\n'.join(e.to_json() for e in runs[0].events))
        self.assertIn('event_id=1 type=run.start component=cli.ask severity=info', err)
        self.assertIn('event_id=4 type=run.end component=cli.ask severity=info', err)
        self.assertIn('dropped_event_count=0', err)
        client.complete.assert_called_once()

    def test_provider_request_and_response_bracket_complete(self):
        actual = RunContext(d.DiagnosticMode.NORMAL)
        def complete(_):
            self.assertEqual(actual.events[-1].type, d.EventType.PROVIDER_REQUEST)
            return Reply(CONTENT, {})
        with patch('llmtool.diagnostics_runtime.RunContext', return_value=actual), \
             patch('llmtool.cli.resolve_config', return_value=configured()), \
             patch('llmtool.cli.api_key'), patch('llmtool.cli.create_client') as factory, \
             patch('llmtool.cli.sys.stdin', None), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            factory.return_value.complete.side_effect = complete
            self.assertEqual(cli.main(['ask', PROMPT]), 0)
        self.assertEqual([e.type.value for e in actual.events],
                         ['run.start', 'provider.request', 'provider.response', 'run.end'])
        factory.return_value.complete.assert_called_once()

    def test_expected_provider_failure_first_line_and_code(self):
        exc = coded_error('gateway request timed out', 'PROVIDER_TIMEOUT', 'provider.openai-compatible', retryable=True)
        status, out, err, client, runs, *_ = self.invoke(fail=exc)
        self.assertEqual((status, out), (1, ''))
        self.assertRegex(err, r'^llmtool: gateway request timed out\nllmtool: code=PROVIDER_TIMEOUT run_id=[0-9a-f]{32} trace=not-saved\n$')
        self.assertEqual(runs[0].final_status, 'error')
        self.assertEqual([e.type.value for e in runs[0].events],
                         ['run.start', 'provider.request', 'error', 'run.end'])
        client.complete.assert_called_once()

    def test_incomplete_result_exit_one_warning_end(self):
        exc = coded_error('incomplete result', 'MODEL_OUTPUT_INCOMPLETE', 'provider.openai')
        status, _, _, client, runs, *_ = self.invoke(fail=exc)
        self.assertEqual(status, 1)
        self.assertEqual(runs[0].events[-1].severity, d.DiagnosticSeverity.WARNING)
        self.assertEqual(runs[0].final_status, 'incomplete')
        client.complete.assert_called_once()

    def test_event_failure_does_not_change_success_call_count_or_output(self):
        for flag in ([], ['--verbose'], ['--debug']):
            with self.subTest(flag=flag), patch.object(d, 'DiagnosticEvent', side_effect=ValueError(MARKER)):
                status, out, err, client, runs, *_ = self.invoke(['ask', PROMPT] + flag)
            self.assertEqual((status, out), (0, CONTENT + '\n'))
            client.complete.assert_called_once()
            self.assertGreater(runs[0].dropped_event_count, 0)
            self.assertNotIn(MARKER, err)
            if not flag:
                self.assertEqual(err, '')
            else:
                self.assertTrue(err.startswith('elapsed_seconds=0.1\nmodel=qwen/test\ntotal_tokens=7\n'))

    def test_invalid_provider_metadata_is_nonfatal(self):
        status, out, err, client, runs, *_ = self.invoke(['ask', PROMPT, '--debug'], metadata={'model': 'm' * 129})
        self.assertEqual((status, out), (0, CONTENT + '\n'))
        self.assertGreater(runs[0].dropped_event_count, 0)
        self.assertNotIn('m' * 129, err)
        client.complete.assert_called_once()

    def test_reply_metadata_access_failure_is_secondary(self):
        class ReplyWithUnavailableMetadata:
            content = CONTENT

            @property
            def metadata(self):
                raise ValueError(MARKER)

        status, out, err, client, runs, *_ = self.invoke(reply=ReplyWithUnavailableMetadata())
        self.assertEqual((status, out, err), (0, CONTENT + '\n', ''))
        self.assertEqual(runs[0].dropped_event_count, 1)
        client.complete.assert_called_once()

    def test_adapter_or_observer_initialization_failure_is_nonfatal(self):
        with patch.object(RunContext, 'response', side_effect=ValueError(MARKER)):
            status, out, _, client, runs, *_ = self.invoke()
        self.assertEqual((status, out), (0, CONTENT + '\n'))
        client.complete.assert_called_once()
        self.assertEqual(runs[0].dropped_event_count, 1)
        # Actual factory creation is inside ask's containment boundary.
        with patch('llmtool.diagnostics_runtime.RunContext', side_effect=ValueError(MARKER)), \
             patch('llmtool.cli.resolve_config', return_value=configured()), \
             patch('llmtool.cli.api_key'), patch('llmtool.cli.create_client') as factory, \
             patch('llmtool.cli.sys.stdin', None), redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            factory.return_value.complete.return_value = Reply(CONTENT, {})
            self.assertEqual(cli.main(['ask', PROMPT]), 0)
            self.assertEqual((out.getvalue(), err.getvalue()), (CONTENT + '\n', ''))
            factory.return_value.complete.assert_called_once()

    def test_input_failure_prevents_config_and_provider(self):
        status, out, err, client, _, resolve, credential, factory = self.invoke(['ask', ''])
        # Empty actual content is checked after readiness, just like existing ask.
        self.assertEqual((status, out), (1, ''))
        self.assertIn('code=USER_INPUT_ERROR', err)
        resolve.assert_called_once()
        credential.assert_not_called()
        factory.assert_not_called()
        client.complete.assert_not_called()
        status, _, err, _, _, resolve, credential, factory = self.invoke(['ask', 'q', '--file', 'file'])
        self.assertEqual(status, 1)
        self.assertIn('code=USER_INPUT_ERROR', err)
        resolve.assert_not_called()
        credential.assert_not_called()
        factory.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'queued pipe probe is POSIX')
    def test_real_queued_pipe_conflict_before_config(self):
        reader, writer = os.pipe()
        try:
            os.write(writer, b'queued data')
            with os.fdopen(reader, 'r') as source:
                status, _, err, _, _, resolve, credential, factory = self.invoke(input_stream=source)
            self.assertEqual(status, 1)
            self.assertIn('code=USER_INPUT_ERROR', err)
            resolve.assert_not_called()
            credential.assert_not_called()
            factory.assert_not_called()
        finally:
            os.close(writer)

    def test_readiness_before_stdin_and_credentials(self):
        source = Mock()
        source.isatty.return_value = False
        source.buffer.read.side_effect = AssertionError('must not consume stdin')
        exc = coded_error('provider model is not configured', 'CONFIG_PROVIDER_NOT_READY', 'provider.openai')
        status, _, err, _, _, _, credential, factory = self.invoke(['ask'], configure_fail=exc, input_stream=source)
        self.assertEqual(status, 1)
        self.assertIn('code=CONFIG_PROVIDER_NOT_READY', err)
        source.buffer.read.assert_not_called()
        credential.assert_not_called()
        factory.assert_not_called()

    def test_config_invalid_code(self):
        status, _, err, _, _, _, credential, factory = self.invoke(configure_fail=LLMToolError('invalid configuration'))
        self.assertEqual(status, 1)
        self.assertTrue(err.startswith('llmtool: invalid configuration\n'))
        self.assertIn('code=CONFIG_INVALID', err)
        credential.assert_not_called()
        factory.assert_not_called()

    def test_auth_credential_missing_at_actual_environment_lookup(self):
        config = configured()
        config['providers']['default']['api_key_env'] = 'A11A2_TEST_CREDENTIAL'
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(LLMToolError) as caught:
            api_key(config)
        self.assertEqual(caught.exception.diagnostic.code, d.ErrorCode.AUTH_CREDENTIAL_MISSING)
        with patch('llmtool.cli.api_key', side_effect=caught.exception), \
             patch('llmtool.cli.resolve_config', return_value=config), \
             patch('llmtool.cli.create_client') as client, patch('llmtool.cli.sys.stdin', None), \
             redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(['ask', PROMPT]), 1)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('code=AUTH_CREDENTIAL_MISSING', err.getvalue())
        client.assert_not_called()

    def test_unexpected_internal_exception_is_never_stringified(self):
        class PrivateException(Exception):
            def __str__(self):
                raise AssertionError('private exception stringified')
        status, out, err, client, runs, *_ = self.invoke(['ask', PROMPT, '--debug'], fail=PrivateException(MARKER))
        self.assertEqual((status, out), (1, ''))
        self.assertTrue(err.startswith('llmtool: internal error; details omitted\n'))
        self.assertIn('code=INTERNAL_UNEXPECTED', err)
        self.assertNotIn(MARKER, err)
        self.assertNotIn('Traceback', err)
        self.assertEqual(runs[0].final_status, 'error')
        client.complete.assert_called_once()

    def test_interrupt_keeps_exit_130_without_retry(self):
        status, out, err, client, runs, *_ = self.invoke(fail=KeyboardInterrupt())
        self.assertEqual((status, out, err), (130, '', 'llmtool: interrupted\n'))
        self.assertEqual(runs[0].final_status, 'interrupted')
        self.assertEqual(runs[0].events[-1].severity, d.DiagnosticSeverity.WARNING)
        client.complete.assert_called_once()

    def test_broken_pipe_keeps_exit_zero(self):
        class ClosedPipe(io.StringIO):
            def write(self, value):
                raise BrokenPipeError

        actual = RunContext()
        with patch('llmtool.diagnostics_runtime.RunContext', return_value=actual), \
             patch('llmtool.cli.resolve_config', return_value=configured()), patch('llmtool.cli.api_key'), \
             patch('llmtool.cli.create_client') as factory, patch('llmtool.cli.sys.stdin', None), \
             patch('llmtool.cli.sys.stdout', ClosedPipe()), redirect_stderr(io.StringIO()):
            factory.return_value.complete.return_value = Reply(CONTENT, {})
            self.assertEqual(cli.main(['ask', PROMPT]), 0)
        self.assertEqual(actual.final_status, 'output_closed')
        factory.return_value.complete.assert_called_once()

    def test_usage_failure_creates_no_observer(self):
        for argv in ([], ['ask', '--unknown'], ['review-diff', '--debug', 'q']):
            with self.subTest(argv=argv), patch('llmtool.diagnostics_runtime.RunContext') as factory, \
                 redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
                cli.main(argv)
            self.assertEqual(caught.exception.code, 2)
            self.assertNotIn('run_id', err.getvalue())
            factory.assert_not_called()

    def test_non_ask_expected_error_and_success_unchanged(self):
        with patch('llmtool.diagnostics_runtime.RunContext', side_effect=AssertionError('ask only')), \
             patch('llmtool.cli.resolve_config', side_effect=LLMToolError('legacy error')), \
             patch('llmtool.cli.sys.stdin', None), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(['review-diff', 'q']), 1)
        self.assertEqual(err.getvalue(), 'llmtool: legacy error\n')
        with patch('llmtool.diagnostics_runtime.RunContext', side_effect=AssertionError('ask only')), \
             patch('llmtool.cli.resolve_config', return_value=configured()), patch('llmtool.cli.api_key'), \
             patch('llmtool.cli.create_client') as factory, patch('llmtool.cli.sys.stdin', None), \
             redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            factory.return_value.complete.return_value = Reply('legacy output', {})
            self.assertEqual(cli.main(['review-diff', 'q']), 0)
        self.assertEqual((out.getvalue(), err.getvalue()), ('legacy output\n', ''))
        factory.return_value.complete.assert_called_once()

    def test_non_ask_unexpected_errors_are_not_globally_reclassified(self):
        with patch('llmtool.cli.resolve_config', side_effect=RuntimeError(MARKER)), \
             patch('llmtool.cli.sys.stdin', None), self.assertRaises(RuntimeError):
            cli.main(['review-diff', 'q'])

    def test_direct_ask_no_diagnostics_io_network_shell_or_home_lookup(self):
        class GuardedEnvironment(dict):
            def get(self, key, default=None):
                if key == 'LLMTOOL_HOME':
                    raise AssertionError('diagnostics home must not be consulted')
                return super().get(key, default)

            def __getitem__(self, key):
                if key == 'LLMTOOL_HOME':
                    raise AssertionError('diagnostics home must not be consulted')
                return super().__getitem__(key)

        with ExitStack() as stack:
            for target in ('builtins.open', 'os.open', 'os.mkdir', 'os.makedirs',
                           'tempfile.TemporaryDirectory', 'tempfile.mkstemp', 'socket.socket',
                           'subprocess.Popen'):
                stack.enter_context(patch(target, side_effect=AssertionError('no diagnostics I/O')))
            stack.enter_context(patch('os.environ', GuardedEnvironment(LLMTOOL_HOME='/must/not/access')))
            status, out, err, client, *_ = self.invoke(['ask', PROMPT, '--debug'])
        self.assertEqual((status, out), (0, CONTENT + '\n'))
        self.assertNotIn('/must/not/access', err)
        client.complete.assert_called_once()

    def test_os_read_failure_has_local_io_code_without_credential_or_provider(self):
        source = Mock()
        source.isatty.return_value = False
        source.buffer.read.side_effect = OSError(MARKER)
        status, out, err, _, _, _, credential, factory = self.invoke(['ask'], input_stream=source)
        self.assertEqual((status, out), (1, ''))
        self.assertTrue(err.startswith('llmtool: cannot read input as UTF-8 text\n'))
        self.assertIn('code=LOCAL_IO_ERROR', err)
        self.assertNotIn(MARKER, err)
        credential.assert_not_called()
        factory.assert_not_called()

    def test_binary_and_oversize_input_keep_legacy_messages_and_input_code(self):
        for raw, message in ((b'\xff', 'input must be UTF-8 text, not binary'),
                             (b'x' * 33, 'input exceeds 16 MiB')):
            source = Mock()
            source.isatty.return_value = False
            source.buffer.read.return_value = raw
            with self.subTest(message=message), patch('llmtool.inputs.INPUT_LIMIT', 32):
                status, out, err, _, _, _, credential, factory = self.invoke(['ask'], input_stream=source)
            self.assertEqual((status, out), (1, ''))
            self.assertTrue(err.startswith('llmtool: ' + message + '\n'))
            self.assertIn('code=USER_INPUT_ERROR', err)
            credential.assert_not_called()
            factory.assert_not_called()

    def test_runtime_ast_has_no_execution_or_direct_filesystem_access(self):
        from llmtool import diagnostics_runtime
        tree = ast.parse(Path(diagnostics_runtime.__file__).read_text())
        forbidden = {'subprocess', 'socket', 'pathlib', 'tempfile', 'fcntl', 'os', 'logging'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertFalse({a.name.split('.')[0] for a in node.names} & forbidden)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {'open', 'exec', 'eval'})


class CredentialLayerTests(unittest.TestCase):
    def test_common_token_shapes_rejected_with_fixed_safe_error(self):
        values = ['gh' + prefix + '_' + 'a' * 36 for prefix in 'pousr']
        values += ['github_pat_' + 'A' * 40]
        values += ['xox' + prefix + '-' + '1' * 12 + '-' + 'a' * 24 for prefix in 'abprs']
        values += ['eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl',
                   'eyJhbGciOiJIUzI1NiJ9.YWJjZGVmZ2hp.c2lnbmF0dXJl',
                   'Basic dXNlcjpwYXNzd29yZA==']
        for value in values:
            with self.subTest(prefix=value[:4]), self.assertRaises(ValueError) as caught:
                d.safe_diagnostic_text(value)
            self.assertEqual(str(caught.exception), 'diagnostic text contains recognizable credential material')
            self.assertNotIn(value, str(caught.exception))

    def test_short_names_english_status_and_redacted_values_allowed(self):
        for value in ('ghp_short', 'xoxb-short', 'JWT format is three segments',
                      'Basic setup failed', 'Basic authentication rejected', 'Basic credentials unavailable',
                      'provider authorization rejected', 'Authorization header missing',
                      'token=missing for profile', 'secret: not configured', 'api_key: <redacted>',
                      'api_key: [redacted]', 'token=***', 'password=****', 'secret=' + '*' * 100,
                      'api_key="<redacted>"', "token='[redacted]'", 'token=redacted'):
            with self.subTest(value=value):
                self.assertEqual(d.safe_diagnostic_text(value), value)


class HTTPMappingTests(unittest.TestCase):
    def clients(self):
        provider = configured()['providers']['default']
        compatible = OpenAICompatibleClient(provider, DEFAULTS['generation'])
        provider = {**provider, 'type': 'openai', 'base_url': None, 'api_key': 'fake-test-key'}
        official = OpenAIProvider(provider, DEFAULTS['generation'], 'fake-test-key')
        for client in (compatible, official):
            client.opener.open = Mock()
            yield client

    def test_http_status_codes_and_numeric_causes_without_retry(self):
        for client in self.clients():
            for status, code in ((401, 'AUTH_PROVIDER_REJECTED'), (403, 'AUTH_PROVIDER_REJECTED'),
                                 (429, 'PROVIDER_RATE_LIMITED'), (500, 'PROVIDER_HTTP_ERROR')):
                with self.subTest(provider=client.TYPE, status=status):
                    client.opener.open.reset_mock()
                    client.opener.open.side_effect = HTTPError('http://private.invalid', status, MARKER, {}, io.BytesIO(MARKER.encode()))
                    with self.assertRaises(LLMToolError) as caught:
                        client.complete([{'role': 'user', 'content': PROMPT}])
                    obj = caught.exception.diagnostic
                    self.assertEqual(obj.code.value, code)
                    self.assertEqual(obj.safe_cause['http_status'], status)
                    self.assertFalse(obj.fallback_allowed)
                    self.assertEqual(obj.retryable, status == 429)
                    self.assertNotIn(MARKER, str(caught.exception))
                    client.opener.open.assert_called_once()

    def test_timeout_connection_and_transport_sites(self):
        for client in self.clients():
            for failure, code in ((socket.timeout(MARKER), 'PROVIDER_TIMEOUT'),
                                  (URLError(socket.timeout(MARKER)), 'PROVIDER_TIMEOUT'),
                                  (URLError(MARKER), 'NETWORK_PROVIDER_UNREACHABLE'),
                                  (OSError(MARKER), 'PROVIDER_RUNTIME_ERROR')):
                with self.subTest(provider=client.TYPE, code=code):
                    client.opener.open.reset_mock()
                    client.opener.open.side_effect = failure
                    with self.assertRaises(LLMToolError) as caught:
                        client.complete([{'role': 'user', 'content': PROMPT}])
                    self.assertEqual(caught.exception.diagnostic.code.value, code)
                    self.assertNotIn(MARKER, str(caught.exception))
                    client.opener.open.assert_called_once()

    def test_invalid_oversize_empty_and_incomplete_response_sites(self):
        class Response(io.BytesIO):
            status = 200
        for client in self.clients():
            if client.TYPE == 'openai':
                empty = {'status': 'completed', 'output': []}
                incomplete = {'status': 'incomplete', 'output': []}
            else:
                empty = {'choices': [{'finish_reason': 'stop', 'message': {'content': ''}}]}
                incomplete = {'choices': [{'finish_reason': 'length'}]}
            for raw, limit, code in ((b'{', 16 * 1024 * 1024, 'MODEL_RESPONSE_INVALID'),
                                     (b'x' * 33, 32, 'MODEL_RESPONSE_TOO_LARGE'),
                                     (json.dumps(empty).encode(), 16 * 1024 * 1024, 'MODEL_RESPONSE_INVALID'),
                                     (json.dumps(incomplete).encode(), 16 * 1024 * 1024, 'MODEL_OUTPUT_INCOMPLETE')):
                with self.subTest(provider=client.TYPE, code=code):
                    client.opener.open.reset_mock()
                    client.opener.open.return_value = Response(raw)
                    module = 'llmtool.providers.' + ('openai' if client.TYPE == 'openai' else 'openai_compatible')
                    with patch(module + '.RESPONSE_LIMIT', limit), self.assertRaises(LLMToolError) as caught:
                        client.complete([{'role': 'user', 'content': PROMPT}])
                    self.assertEqual(caught.exception.diagnostic.code.value, code)
                    client.opener.open.assert_called_once()


if __name__ == '__main__':
    unittest.main()
