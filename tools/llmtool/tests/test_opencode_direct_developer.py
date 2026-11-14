import json
from datetime import datetime
import os
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool.opencode_direct_developer import (
    AGENT, MAX_OUTPUT_BYTES, MAX_OUTPUT_TOKENS, MAX_SOURCE_FILE_BYTES, MODEL,
    DirectOpenCodeError, _classify_provider_error, _classify_provider_events,
    _recovered_transport_indices, configuration, parse_json_events, run,
    summarize_events,
)
from llmtool.providers.cli_lifecycle import CLIHostTermination
from llmtool import worker_dispatch


import review_trust_fixture
import worker_policy_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class DirectOpenCodeDeveloperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='llmtool-opencode-direct-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'source.py').write_text('VALUE = 1\n')
        # #1692: no test in this class reads the live worker-dispatch.json.
        worker_policy_fixture.install(self)

    def run_with_valid_binding(self, **kwargs):
        store = kwargs.pop('dispatch_store', object())
        with patch('llmtool.opencode_direct_developer.worker_dispatch.authorize_dispatch'):
            return run(**kwargs, dispatch_store=store, dispatch_task_id='a' * 32,
                       prelaunch_validate=lambda: True)

    def test_direct_adapter_cannot_run_without_dispatch_context(self):
        marker = self.root / 'run-invoked'
        cli = self.fake_cli(self.event('step_start') + '\n',
                            run_invocation_marker=marker)
        with self.assertRaises(DirectOpenCodeError) as caught:
            run(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', prelaunch_validate=lambda: True,
                timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code,
                         'WORKER_DISPATCH_CONTEXT_UNAVAILABLE')
        self.assertFalse(marker.exists())

    def test_paused_policy_blocks_direct_adapter_before_provider_run(self):
        class Store:
            def __init__(self, task_id, allowed_policy_path):
                from llmtool.action_contract import TaskBinding

                binding = {
                    'task_id': task_id, 'run_id': 'b' * 32,
                    'contract_version': 1, 'contract_digest': 'c' * 64,
                }
                developer = {
                    'assignment_id': 'd' * 32, 'state': 'DEVELOPER_DISPATCHING',
                    'provider_profile': 'opencode', 'provider_type': 'opencode-cli',
                    'model': 'opencode/deepseek-v4.1-flash-free',
                    'source_sha': 'e' * 40,
                }
                with patch.object(worker_dispatch, '_POLICY_PATH', allowed_policy_path):
                    transition = worker_dispatch.audit_entry(
                        worker_dispatch.evaluate('opencode-developer', task_id=task_id),
                        'DISPATCH', binding={
                            'assignment_id': developer['assignment_id'],
                            'source_sha': developer['source_sha'],
                            'contract_digest': binding['contract_digest'],
                        })
                self.record = {
                    'schema_version': 2, 'task_id': task_id,
                    'task_binding': TaskBinding(**binding).to_dict(),
                    'developer': developer, 'dispatch_audit': [transition],
                }

            def get(self, task_id):
                if task_id != self.record['task_id']:
                    raise worker_dispatch.WorkerDispatchDenied('TEST_TASK_MISMATCH')
                return json.loads(json.dumps(self.record))

            def append_dispatch_audit(self, task_id, entry, *, expected=None):
                if (task_id != self.record['task_id'] or type(expected) is not dict or
                        any(self.record.get(key) != value for key, value in expected.items())):
                    raise worker_dispatch.WorkerDispatchDenied('TEST_STORE_CONFLICT')
                self.record['dispatch_audit'].append(json.loads(json.dumps(entry)))
                return json.loads(json.dumps(self.record))

        # #1692: the shared test policy, never the live worker-dispatch.json.
        allowed_policy_path = worker_policy_fixture.write(
            self.root / 'worker-dispatch-allowed.json',
            worker_policy_fixture.policy({'opencode-developer': 'ALLOWED'}))
        paused_policy_path = worker_policy_fixture.write(
            self.root / 'worker-dispatch-paused.json',
            worker_policy_fixture.policy({'opencode-developer': 'PAUSED'}))
        store = Store('a' * 32, allowed_policy_path)
        run_marker = self.root / 'provider-run-invoked'
        cli = self.fake_cli(self.event('step_start') + '\n',
                            run_invocation_marker=run_marker)
        with patch.object(worker_dispatch, '_POLICY_PATH', paused_policy_path):
            with self.assertRaises(DirectOpenCodeError) as caught:
                run(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                    prompt='bounded task prompt', prelaunch_validate=lambda: True,
                    dispatch_store=store, dispatch_task_id='a' * 32,
                    timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'OWNER_PAUSED')
        self.assertFalse(run_marker.exists())
        refusal = store.record['dispatch_audit'][-1]
        self.assertEqual(refusal['decision'], 'BLOCKED')
        self.assertEqual(refusal['worker_key'], 'opencode-developer')
        self.assertEqual(refusal['reason_code'], 'OWNER_PAUSED')
        self.assertEqual(refusal['assignment_id'], 'd' * 32)
        self.assertEqual(refusal['source_sha'], 'e' * 40)
        self.assertEqual(refusal['contract_digest'], 'c' * 64)

    @staticmethod
    def event(kind, **fields):
        return json.dumps({'type': kind, 'sessionID': 'ses_Abc123', **fields})

    def test_config_denies_tools_except_exact_read_and_edit_scope(self):
        value = configuration(read_paths=['source.py'], write_paths=['source.py'])
        self.assertEqual(value['providers']['opencode']['models'][
            'deepseek-v4.1-flash-free']['limit']['output'], 32768)
        self.assertEqual(value['model'], MODEL)
        self.assertEqual(value['default_agent'], AGENT)
        self.assertEqual(value['permissions'][0], {
            'action': '*', 'resource': '*', 'effect': 'deny'})
        self.assertEqual(value['permissions'][1:], [
            {'action': 'read', 'resource': 'source.py', 'effect': 'allow'},
            {'action': 'edit', 'resource': 'source.py', 'effect': 'allow'},
        ])
        self.assertEqual(value['mcp'], {'servers': {}})
        self.assertEqual(value['agents'][AGENT]['steps'], 12)
        self.assertEqual(value['agents'][AGENT]['permissions'], value['permissions'])
        self.assertEqual(value['policies'][-1], {
            'action': 'integration.use', 'resource': 'opencode#*', 'effect': 'allow'})
        self.assertEqual([(p['resource'], p['effect']) for p in value['policies']
                          if p['action'] == 'integration.use'],
                         [('*', 'deny'), ('opencode#*', 'allow')])

    def test_configured_agent_steps_are_bounded(self):
        value = configuration(read_paths=['source.py'], write_paths=['source.py'], steps=23)
        self.assertEqual(value['agents'][AGENT]['steps'], 23)
        for steps in (2, 51, True, 12.0, '12'):
            with self.subTest(steps=steps), self.assertRaises(DirectOpenCodeError) as caught:
                configuration(read_paths=['source.py'], write_paths=['source.py'],
                              steps=steps)
            self.assertEqual(caught.exception.direct_opencode_code,
                             'OPENCODE_STEPS_INVALID')

    def test_no_final_text_after_step_limit_has_distinct_error(self):
        raw = '\n'.join([self.event('step_finish', part={}) for _ in range(12)]).encode()
        with self.assertRaises(DirectOpenCodeError) as caught:
            parse_json_events(raw)
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_STEPS_EXHAUSTED')

    def test_json_event_parser_binds_one_session_and_reports_usage(self):
        raw = ('\n'.join((
            self.event('step_start'),
            self.event('text', part={'text': 'change ready'}),
            self.event('step_finish', part={'tokens': {'input': 11, 'output': 7}}),
        )) + '\n').encode()
        sid, text, usage = parse_json_events(raw)
        self.assertEqual(sid, 'ses_Abc123')
        self.assertEqual(text, 'change ready')
        self.assertEqual(usage, {'prompt_tokens': 11, 'completion_tokens': 7,
                                 'total_tokens': 18})

    def test_summarize_events_counts_edit_tool_uses(self):
        raw = ('\n'.join((
            self.event('step_start'),
            self.event('tool_use', part={'tool': 'edit',
                                         'state': {'input': {'filePath': 'a.py'}}}),
            self.event('tool_use', part={'tool': 'edit',
                                         'state': {'input': {'filePath': 'b.py'}}}),
            self.event('tool_use', part={'tool': 'read',
                                         'state': {'input': {'filePath': 'c.py'}}}),
            self.event('step_finish', part={}),
            self.event('step_finish', part={}),
            self.event('step_finish', part={}),
        )) + '\n').encode()
        summary = summarize_events(raw)
        self.assertEqual(summary['event_counts'],
                         {'step_start': 1, 'tool_use': 3, 'step_finish': 3})
        self.assertEqual(summary['text_chars'], 0)
        self.assertEqual(summary['tool_use_count'], 3)
        self.assertEqual(summary['edit_tool_uses'], 2)
        self.assertEqual(summary['edited_file_count'], 2)
        self.assertEqual(summary['step_finishes'], 3)
        self.assertEqual(summary['step_limit'], 12)

    def test_summarize_events_reports_other_and_integer_only(self):
        marker = 'marker-0123456789'
        raw = ('\n'.join((
            self.event('text', part={'text': marker}),
            self.event('unknown'),
        )) + '\n').encode()
        summary = summarize_events(raw)
        self.assertEqual(summary['event_counts']['other'], 1)
        self.assertEqual(summary['text_chars'], len(marker))

        def assert_int(value):
            if isinstance(value, dict):
                for child in value.values():
                    assert_int(child)
            else:
                self.assertIs(type(value), int)

        assert_int(summary)

    def test_summarize_events_counts_valid_prefix_on_malformed_line(self):
        raw = (self.event('step_start') + '\n' + '{not-json').encode()
        summary = summarize_events(raw)
        self.assertEqual(summary['event_counts'], {'step_start': 1})
        self.assertEqual(summary['step_finishes'], 0)
        self.assertEqual(summary['step_limit'], 12)

    def test_output_cap_is_16_mib_and_parser_accepts_output_over_1_mib(self):
        self.assertEqual(MAX_OUTPUT_BYTES, 16 * 1024 * 1024)
        message = 'x' * (1024 * 1024 + 1)
        raw = (self.event('text', part={'text': message}) + '\n').encode()
        self.assertGreater(len(raw), 1024 * 1024)
        self.assertLessEqual(len(raw), MAX_OUTPUT_BYTES)
        self.assertEqual(parse_json_events(raw)[1], message)

    def test_parser_rejects_output_above_cap_with_typed_error(self):
        with patch('llmtool.opencode_direct_developer.MAX_OUTPUT_BYTES', 128):
            with self.assertRaises(DirectOpenCodeError) as caught:
                parse_json_events(b'x' * 129)
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_OUTPUT_TOO_LARGE')

    def test_streaming_rejects_output_above_cap_and_cleans_up(self):
        cli = self.fake_cli('x' * 129)
        with patch('llmtool.opencode_direct_developer.MAX_OUTPUT_BYTES', 128), \
                patch('llmtool.opencode_direct_developer.parse_json_events') as parser:
            with self.assertRaises(DirectOpenCodeError) as caught:
                self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                    write_paths=['source.py'], prompt='bounded task prompt',
                    timeout_seconds=5, executable=cli)
        parser.assert_not_called()
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_OUTPUT_TOO_LARGE')
        self.assertEqual(caught.exception.cleanup_status, 'CONFIRMED')

    def test_usage_missing_is_unknown_not_zero(self):
        raw = (self.event('step_start') + '\n' +
               self.event('text', text='done') + '\n').encode()
        self.assertIsNone(parse_json_events(raw)[2])

    def test_one_missing_step_usage_keeps_total_usage_unknown(self):
        raw = ('\n'.join((
            self.event('step_finish', part={'tokens': {'input': 11, 'output': 7}}),
            self.event('step_finish', part={}),
        )) + '\n').encode()
        self.assertIsNone(parse_json_events(raw)[2])

    def test_events_with_mixed_sessions_or_invalid_json_fail_closed(self):
        wrong = json.dumps({'type': 'text', 'sessionID': 'ses_Other'})
        for raw in ((self.event('step_start') + '\n' + wrong + '\n').encode(),
                    b'{not json}\n', b'{}\n'):
            with self.subTest(raw=raw), self.assertRaises(DirectOpenCodeError):
                parse_json_events(raw)

    def test_structured_tool_refusal_is_typed_without_exposing_output(self):
        denied = self.event('tool_use', part={
            'state': {'status': 'error', 'error': {'code': 'PERMISSION_DENIED',
                                                'message': 'FAKE_SECRET_12345678'}}})
        with self.assertRaises(DirectOpenCodeError) as caught:
            parse_json_events((denied + '\n').encode())
        self.assertEqual(caught.exception.direct_opencode_code, 'TOOL_DENIED')
        self.assertNotIn('FAKE_SECRET_12345678', str(caught.exception))
        # Free-form model text cannot manufacture a permission verdict.
        sid, text, _ = parse_json_events((self.event(
            'text', text='PERMISSION_DENIED FAKE_SECRET_12345678') + '\n').encode())
        self.assertEqual(sid, 'ses_Abc123')
        self.assertIn('PERMISSION_DENIED', text)
        # Malformed structured values are untrusted data, never an exception leak.
        with self.assertRaises(DirectOpenCodeError) as malformed:
            parse_json_events((self.event('error', error={'code': ['PERMISSION_DENIED']})
                               + '\n').encode())
        self.assertEqual(malformed.exception.direct_opencode_code,
                         'OPENCODE_PROVIDER_FAILED')

    def test_provider_auth_error_in_stream_is_typed_without_exposing_message(self):
        marker = 'PROVIDER_AUTH_MESSAGE_SECRET_123'
        event = self.event('error', error={
            'type': 'provider.auth', 'message': marker,
        })
        with self.assertRaises(DirectOpenCodeError) as caught:
            parse_json_events((event + '\n').encode())
        error = caught.exception
        self.assertEqual(error.direct_opencode_code, 'OPENCODE_AUTH_REQUIRED')
        self.assertNotIn(marker, str(error))
        self.assertNotIn(marker, repr(error.args))
        self.assertNotIn(marker, repr(vars(error)))

    def test_nonzero_provider_auth_error_is_typed_without_exposing_message(self):
        task_id, run_id = 'a' * 32, 'b' * 32
        marker = 'PROVIDER_AUTH_MESSAGE_SECRET_456'

        class Store:
            def get(self, selected):
                return {'task_id': selected, 'run_id': run_id}

        body = self.event('error', error={
            'type': 'provider.auth', 'message': marker,
        }) + '\n'
        cli = self.fake_cli(body, exit_code=7)
        with patch('llmtool.opencode_direct_developer.worker_dispatch.authorize_dispatch'):
            with self.assertRaises(DirectOpenCodeError) as caught:
                run(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                    prompt='bounded task prompt', prelaunch_validate=lambda: True,
                    dispatch_store=Store(), dispatch_task_id=task_id,
                    timeout_seconds=5, executable=cli)
        error = caught.exception
        self.assertEqual(error.direct_opencode_code, 'OPENCODE_AUTH_REQUIRED')
        self.assertNotIn(marker, str(error))
        self.assertNotIn(marker, repr(error.args))
        self.assertNotIn(marker, repr(vars(error)))

    def test_non_auth_provider_error_stays_provider_failed(self):
        marker = 'PROVIDER_FAILURE_MESSAGE_SECRET_789'
        event = self.event('error', error={
            'type': 'provider.unavailable', 'message': marker,
        })
        with self.assertRaises(DirectOpenCodeError) as caught:
            parse_json_events((event + '\n').encode())
        error = caught.exception
        self.assertEqual(error.direct_opencode_code, 'OPENCODE_PROVIDER_FAILED')
        self.assertNotIn(marker, str(error))
        self.assertNotIn(marker, repr(error.args))
        self.assertNotIn(marker, repr(vars(error)))

    def test_provider_transport_error_is_recovered_by_later_structured_completion(self):
        transport = self.event('error', error={
            'type': 'provider.transport', 'message': 'UNTRUSTED TRANSPORT PROSE',
        })
        completion = self.event('step_finish', part={})
        raw = (transport + '\n' + completion + '\n').encode()
        self.assertIsNone(_classify_provider_error(raw))
        sid, text, _ = parse_json_events(raw)
        self.assertEqual((sid, text), ('ses_Abc123', None))

    def test_exit_zero_transport_recovery_requires_ordered_completion(self):
        transport = {'type': 'provider.transport'}
        unavailable = {'type': 'provider.unavailable'}
        cases = (
            ('finish before transport', (
                {'type': 'step_finish', 'sessionID': 'ses_Abc123'},
                {'type': 'error', 'sessionID': 'ses_Abc123', 'error': transport},
            ), set()),
            ('only earlier transport recovered', (
                {'type': 'error', 'sessionID': 'ses_Abc123', 'error': transport},
                {'type': 'step_finish', 'sessionID': 'ses_Abc123'},
                {'type': 'error', 'sessionID': 'ses_Abc123', 'error': transport},
            ), {0}),
            ('text and finish before transport', (
                {'type': 'text', 'sessionID': 'ses_Abc123', 'part': {'text': 'done'}},
                {'type': 'step_finish', 'sessionID': 'ses_Abc123'},
                {'type': 'error', 'sessionID': 'ses_Abc123', 'error': transport},
            ), set()),
            ('later provider error remains fatal', (
                {'type': 'error', 'sessionID': 'ses_Abc123', 'error': transport},
                {'type': 'step_finish', 'sessionID': 'ses_Abc123'},
                {'type': 'error', 'sessionID': 'ses_Abc123', 'error': unavailable},
            ), {0}),
        )
        for name, events, recovered_indices in cases:
            with self.subTest(name=name):
                self.assertEqual(_recovered_transport_indices(events),
                                 recovered_indices)
                raw = ('\n'.join(json.dumps(event) for event in events) + '\n').encode()
                with self.assertRaises(DirectOpenCodeError) as caught:
                    self.run_with_valid_binding(
                        root=self.root, read_paths=['source.py'],
                        write_paths=['source.py'], prompt='bounded task prompt',
                        timeout_seconds=5, executable=self.fake_cli(raw.decode(),
                                                                    exit_code=0))
                self.assertEqual(caught.exception.direct_opencode_code,
                                 'OPENCODE_PROVIDER_FAILED')

    def test_transport_error_text_only_or_different_session_is_not_recovery(self):
        transport = self.event('error', error={'type': 'provider.transport'})
        text_only = self.event('text', part={'text': 'completed'})
        other_session_finish = json.dumps({
            'type': 'step_finish', 'sessionID': 'ses_Other', 'part': {},
        })
        for completion in (text_only, other_session_finish):
            with self.subTest(completion=completion):
                raw = (transport + '\n' + completion + '\n').encode()
                self.assertEqual(_classify_provider_error(raw),
                                 'OPENCODE_PROVIDER_FAILED')
                with self.assertRaises(DirectOpenCodeError) as caught:
                    parse_json_events(raw)
                self.assertEqual(caught.exception.direct_opencode_code,
                                 'OPENCODE_PROVIDER_FAILED')

        missing_session = {'type': 'error', 'error': {'type': 'provider.transport'}}
        malformed_session = {
            'type': 'error', 'sessionID': 'not-a-session',
            'error': {'type': 'provider.transport'},
        }
        for transport_event in (missing_session, malformed_session):
            raw = (json.dumps(transport_event) + '\n' +
                   self.event('step_finish', part={}) + '\n').encode()
            self.assertEqual(_classify_provider_error(raw),
                             'OPENCODE_PROVIDER_FAILED')
            with self.assertRaises(DirectOpenCodeError) as caught:
                parse_json_events(raw)
            self.assertEqual(caught.exception.direct_opencode_code,
                             'OPENCODE_PROVIDER_FAILED')

    def test_transport_recovery_scans_large_event_stream(self):
        class CountingList(list):
            def __init__(self, values):
                super().__init__(values)
                self.element_reads = 0

            def __getitem__(self, index):
                value = super().__getitem__(index)
                self.element_reads += len(value) if isinstance(index, slice) else 1
                return value

            def __iter__(self):
                for value in super().__iter__():
                    self.element_reads += 1
                    yield value

        count = 20_000
        transport = {'type': 'error', 'sessionID': 'ses_Abc123',
                     'error': {'type': 'provider.transport'}}
        events = CountingList(transport.copy() for _ in range(count))
        events.append({'type': 'step_finish', 'sessionID': 'ses_Abc123'})
        recovered = _recovered_transport_indices(events)
        self.assertEqual(len(recovered), count)
        self.assertEqual(min(recovered), 0)
        self.assertEqual(max(recovered), count - 1)
        self.assertLessEqual(events.element_reads, 2 * len(events))

    def test_non_dict_json_line_keeps_later_error_classification(self):
        failed = (b'[]\n' + self.event(
            'error', error={'type': 'provider.unavailable'}).encode() + b'\n')
        self.assertEqual(_classify_provider_error(failed),
                         'OPENCODE_PROVIDER_FAILED')

        invalid = (b'[]\n' + self.event('step_start').encode() + b'\n')
        with self.assertRaises(DirectOpenCodeError) as caught:
            parse_json_events(invalid)
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_OUTPUT_INVALID')

    def test_output_truncation_classification_does_not_serialize_events(self):
        nested = {}
        for _ in range(1_100):
            nested = {'nested': nested}
        event = {'type': 'text', 'sessionID': 'ses_Abc123', 'deep': nested}
        self.assertIsNone(_classify_provider_events([event]))

    def test_provider_transport_error_without_later_completion_stays_failed(self):
        transport = self.event('error', error={
            'type': 'provider.transport', 'message': 'UNTRUSTED TRANSPORT PROSE',
        })
        for events in ((transport,),
                       (self.event('step_start'), transport)):
            with self.subTest(events=events):
                raw = ('\n'.join(events) + '\n').encode()
                self.assertEqual(_classify_provider_error(raw),
                                 'OPENCODE_PROVIDER_FAILED')

    def test_run_accepts_recovered_transport_and_rejects_failure_variants(self):
        transport = self.event('error', error={'type': 'provider.transport'})
        finish = self.event('step_finish', part={'tokens': {'input': 2, 'output': 3}})
        text = self.event('text', part={'text': 'completed'})
        success = self.run_with_valid_binding(
            root=self.root, read_paths=['source.py'], write_paths=['source.py'],
            prompt='bounded task prompt', timeout_seconds=5,
            executable=self.fake_cli('\n'.join((transport, finish, text)) + '\n'))
        self.assertEqual(success.exit_code, 0)
        self.assertEqual((success.session_id, success.text), ('ses_Abc123', 'completed'))

        other_error = self.event('error', error={'type': 'provider.unavailable'})
        cases = (
            ('nonzero after recovery', '\n'.join((transport, finish, text)) + '\n', 7),
            ('later provider error', '\n'.join((transport, other_error, finish, text)) + '\n', 0),
            ('unrecovered transport', transport + '\n', 0),
            ('other error before finish', '\n'.join((other_error, finish, text)) + '\n', 0),
            ('unknown error before finish', '\n'.join((
                self.event('error', error={'type': 'provider.unknown'}),
                finish, text)) + '\n', 0),
        )
        for label, body, exit_code in cases:
            with self.subTest(label=label), self.assertRaises(DirectOpenCodeError) as caught:
                self.run_with_valid_binding(
                    root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                    prompt='bounded task prompt', timeout_seconds=5,
                    executable=self.fake_cli(body, exit_code=exit_code))
            self.assertEqual(caught.exception.direct_opencode_code,
                             'OPENCODE_PROVIDER_FAILED')

    def test_error_classification_survives_a_later_malformed_line(self):
        limit = self.event('error', error={
            'type': 'provider.free-limit', 'message': 'PRIVATE FREE MESSAGE',
        })
        raw = (limit + '\n' + '{truncated json\n').encode()
        self.assertEqual(_classify_provider_error(raw), 'OPENCODE_FREE_LIMIT')
        cli = self.fake_cli(raw.decode(), exit_code=7)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(
                root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_FREE_LIMIT')

    def test_other_provider_errors_keep_classification_after_completion(self):
        cases = (
            (self.event('error', error={'type': 'provider.auth'}),
             'OPENCODE_AUTH_REQUIRED'),
            (self.event('error', error={'type': 'provider.free-limit'}),
             'OPENCODE_FREE_LIMIT'),
            (self.event('error', error={'type': 'provider.quota'}),
             'OPENCODE_FREE_LIMIT'),
            (self.event('error', error={'type': 'provider.rate-limit'}),
             'OPENCODE_RATE_LIMITED'),
            (self.event('tool_use', part={'state': {'status': 'denied'}}),
             'TOOL_DENIED'),
            (self.event('error', error={'type': 'output-limit'}),
             'OPENCODE_OUTPUT_TRUNCATED'),
        )
        completion = self.event('text', part={'text': 'completed'})
        for failure, expected in cases:
            with self.subTest(expected=expected):
                raw = (failure + '\n' + completion + '\n').encode()
                self.assertEqual(_classify_provider_error(raw), expected)

    def test_error_event_without_dict_payload_stays_provider_failed(self):
        event = self.event('error', error='malformed provider payload')
        with self.assertRaises(DirectOpenCodeError) as caught:
            parse_json_events((event + '\n').encode())
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_PROVIDER_FAILED')

    def test_structured_provider_limit_and_rate_codes(self):
        cases = (
            ({'type': 'provider.free-limit', 'message': 'PRIVATE FREE MESSAGE'},
             'OPENCODE_FREE_LIMIT'),
            ({'type': 'provider.quota', 'message': 'PRIVATE QUOTA MESSAGE'},
             'OPENCODE_FREE_LIMIT'),
            ({'type': 'provider.rate-limit', 'message': 'PRIVATE RATE MESSAGE'},
             'OPENCODE_RATE_LIMITED'),
            ({'statusCode': 429, 'message': 'PRIVATE RATE MESSAGE'},
             'OPENCODE_RATE_LIMITED'),
            ({'type': 'provider.unavailable', 'message': 'PRIVATE UNKNOWN MESSAGE'},
             'OPENCODE_PROVIDER_FAILED'),
        )
        for details, expected in cases:
            with self.subTest(details=details):
                event = self.event('error', error=details)
                with self.assertRaises(DirectOpenCodeError) as caught:
                    parse_json_events((event + '\n').encode())
                self.assertEqual(caught.exception.direct_opencode_code, expected)
                self.assertNotIn('PRIVATE', repr(vars(caught.exception)))

    def test_nonzero_structured_provider_limit_and_rate_codes(self):
        cases = (
            ({'type': 'provider.free-limit', 'message': 'PRIVATE FREE MESSAGE'},
             'OPENCODE_FREE_LIMIT'),
            ({'type': 'provider.quota', 'message': 'PRIVATE QUOTA MESSAGE'},
             'OPENCODE_FREE_LIMIT'),
            ({'type': 'provider.rate-limit', 'message': 'PRIVATE RATE MESSAGE'},
             'OPENCODE_RATE_LIMITED'),
            ({'statusCode': 429, 'message': 'PRIVATE RATE MESSAGE'},
             'OPENCODE_RATE_LIMITED'),
            ({'type': 'provider.unavailable', 'message': 'PRIVATE UNKNOWN MESSAGE'},
             'OPENCODE_PROVIDER_FAILED'),
        )
        for details, expected in cases:
            with self.subTest(details=details):
                cli = self.fake_cli(self.event('error', error=details) + '\n', exit_code=7)
                with self.assertRaises(DirectOpenCodeError) as caught:
                    self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                        write_paths=['source.py'], prompt='bounded task prompt',
                        timeout_seconds=5, executable=cli)
                self.assertEqual(caught.exception.direct_opencode_code, expected)
                self.assertNotIn('PRIVATE', repr(vars(caught.exception)))

    def test_output_limit_error_is_truncated_on_zero_and_nonzero_exits(self):
        marker = 'PRIVATE OUTPUT MESSAGE'
        event = self.event('error', error={
            'type': 'output-limit', 'message': marker,
        })
        with self.assertRaises(DirectOpenCodeError) as parsed:
            parse_json_events((event + '\n').encode())
        self.assertEqual(parsed.exception.direct_opencode_code,
                         'OPENCODE_OUTPUT_TRUNCATED')
        self.assertNotIn(marker, repr(vars(parsed.exception)))
        cli = self.fake_cli(event + '\n', exit_code=7)
        with self.assertRaises(DirectOpenCodeError) as nonzero:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                write_paths=['source.py'], prompt='bounded task prompt',
                timeout_seconds=5, executable=cli)
        self.assertEqual(nonzero.exception.direct_opencode_code,
                         'OPENCODE_OUTPUT_TRUNCATED')
        self.assertNotIn(marker, repr(vars(nonzero.exception)))

    def test_unknown_output_limit_fallback_uses_real_cli_message(self):
        marker = 'Response still truncated after two output token limit continuations'
        event = self.event('error', error={
            'type': 'unknown', 'message': marker,
        })
        with self.assertRaises(DirectOpenCodeError) as caught:
            parse_json_events((event + '\n').encode())
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_OUTPUT_TRUNCATED')
        self.assertNotIn(marker, str(caught.exception))
        self.assertNotIn(marker, repr(vars(caught.exception)))
        cli = self.fake_cli(event + '\n', exit_code=7)
        with self.assertRaises(DirectOpenCodeError) as nonzero:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                write_paths=['source.py'], prompt='bounded task prompt',
                timeout_seconds=5, executable=cli)
        self.assertEqual(nonzero.exception.direct_opencode_code,
                         'OPENCODE_OUTPUT_TRUNCATED')
        self.assertNotIn(marker, str(nonzero.exception))
        self.assertNotIn(marker, repr(vars(nonzero.exception)))

    def test_limit_classification_precedence_is_consistent_across_events(self):
        output = self.event('error', error={
            'type': 'output-limit', 'message': 'PRIVATE OUTPUT MESSAGE',
        })
        auth = self.event('error', error={
            'type': 'provider.auth', 'message': 'PRIVATE AUTH MESSAGE',
        })
        denied = self.event('tool_use', part={
            'state': {'status': 'denied'},
        })
        for events, expected in (((output, auth), 'OPENCODE_AUTH_REQUIRED'),
                                 ((auth, output), 'OPENCODE_AUTH_REQUIRED'),
                                 ((output, denied), 'TOOL_DENIED'),
                                 ((denied, output), 'TOOL_DENIED')):
            raw = ('\n'.join(events) + '\n').encode()
            with self.subTest(expected=expected, events=events):
                with self.assertRaises(DirectOpenCodeError) as parsed:
                    parse_json_events(raw)
                self.assertEqual(parsed.exception.direct_opencode_code, expected)
                cli = self.fake_cli(raw.decode(), exit_code=7)
                with self.assertRaises(DirectOpenCodeError) as nonzero:
                    self.run_with_valid_binding(root=self.root,
                        read_paths=['source.py'], write_paths=['source.py'],
                        prompt='bounded task prompt', timeout_seconds=5,
                        executable=cli)
                self.assertEqual(nonzero.exception.direct_opencode_code, expected)
                self.assertNotIn('PRIVATE', repr(vars(nonzero.exception)))

    def test_nonzero_structured_denial_has_bound_timing_and_no_secret_leak(self):
        task_id, run_id = 'a' * 32, 'b' * 32

        class Store:
            def get(self, selected):
                return {'task_id': selected, 'run_id': run_id}

        body = '\n'.join((
            self.event('step_start'),
            self.event('error', error={'code': 'PERMISSION_DENIED',
                                       'message': 'FAKE_SECRET_12345678'}),
        )) + '\n'
        cli = self.fake_cli(body, exit_code=7)
        with patch('llmtool.opencode_direct_developer.worker_dispatch.authorize_dispatch'):
            with self.assertRaises(DirectOpenCodeError) as caught:
                run(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                    prompt='bounded task prompt', prelaunch_validate=lambda: True,
                    dispatch_store=Store(), dispatch_task_id=task_id,
                    timeout_seconds=5, executable=cli)
        error = caught.exception
        self.assertEqual(error.direct_opencode_code, 'TOOL_DENIED')
        self.assertEqual((error.task_id, error.run_id, error.native_session_id),
                         (task_id, run_id, 'ses_Abc123'))
        self.assertLessEqual(datetime.fromisoformat(error.started_at.replace('Z', '+00:00')),
                             datetime.fromisoformat(error.ended_at.replace('Z', '+00:00')))
        self.assertNotIn('FAKE_SECRET_12345678', str(error))
        self.assertNotIn('FAKE_SECRET_12345678', repr(error.__dict__))

    def test_permission_request_stops_live_noninteractive_child_promptly(self):
        body = self.event('permission.asked') + '\n'
        cli = self.fake_cli(body, post_output_delay=10)
        began = time.monotonic()
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                write_paths=['source.py'], prompt='bounded task prompt',
                timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'TOOL_DENIED')
        self.assertEqual(caught.exception.cleanup_status, 'CONFIRMED')
        self.assertLess(time.monotonic() - began, 3)

    def fake_cli(self, body, *, exit_code=0, delay=0, stderr_body='', invocation_marker=None,
                 version_output='opencode v2.0.32\n',
                 version_descendant_marker=None, run_descendant_marker=None,
                 version_mutate_path=None, run_invocation_marker=None,
                 post_output_delay=0, model=MODEL):
        marker_code = (f"open({str(invocation_marker)!r}, 'a').write('called\\n')\n"
                       if invocation_marker else '')
        mutation_code = (f"    open({str(version_mutate_path)!r}, 'w').write('CHANGED\\n')\n"
                         if version_mutate_path else '')
        run_marker_code = (
            f"open({str(run_invocation_marker)!r}, 'a').write(str(os.getpid()) + '\\n')\n"
            if run_invocation_marker else '')
        version_child_code = ''
        if version_descendant_marker:
            version_child_code = (
                'pid = os.fork()\n'
                'if pid == 0:\n'
                '    for fd in (0, 1, 2):\n'
                '        try: os.close(fd)\n'
                '        except OSError: pass\n'
                '    time.sleep(1)\n'
                f"    open({str(version_descendant_marker)!r}, 'w').write('leaked')\n"
                '    os._exit(0)\n'
            )
        run_child_code = ''
        if run_descendant_marker:
            run_child_code = (
                'pid = os.fork()\n'
                'if pid == 0:\n'
                '    for fd in (0, 1, 2):\n'
                '        try: os.close(fd)\n'
                '        except OSError: pass\n'
                '    time.sleep(1)\n'
                f"    open({str(run_descendant_marker)!r}, 'w').write('leaked')\n"
                '    os._exit(0)\n'
            )
        cli = self.root / 'opencode-fixture'
        script = (
            '#!/usr/bin/env python3\n'
            'import json, os, sys, time\n'
            + marker_code +
            "if sys.argv[1:] == ['--version']:\n"
            + ''.join('    ' + line + '\n' for line in version_child_code.splitlines())
            + mutation_code
            + f'    sys.stdout.write({version_output!r})\n'
            '    raise SystemExit(0)\n'
            + run_marker_code +
            "assert sys.argv[1:4] == ['run', '--standalone', '--model']\n"
            f"assert sys.argv[4] == {model!r}\n"
            "assert sys.argv[sys.argv.index('--format') + 1] == 'json'\n"
            "assert sys.argv[sys.argv.index('--agent') + 1] == 'llmtool-direct-developer'\n"
            'config_path = os.environ["OPENCODE_CONFIG"]\n'
            'assert os.path.dirname(config_path) == os.environ["OPENCODE_CONFIG_DIR"]\n'
            'assert os.stat(config_path).st_mode & 0o777 == 0o600\n'
            'assert os.environ["OPENCODE_CONFIG_PROJECT_DISABLE"] == "1"\n'
            'assert "OPENCODE_CONFIG_CONTENT" not in os.environ\n'
            'cfg = json.load(open(config_path, encoding="utf-8"))\n'
            f'assert cfg["model"] == {model!r}\n'
            'assert cfg["permissions"][0]["effect"] == "deny"\n'
            'assert cfg["agents"]["llmtool-direct-developer"]["mode"] == "primary"\n'
            'assert cfg["agents"]["llmtool-direct-developer"]["permissions"] == cfg["permissions"]\n'
            'assert not os.environ.get("OPENAI_API_KEY")\n'
            'sys.stdin.buffer.read()\n'
            + run_child_code +
            f'time.sleep({delay!r})\n'
            f'sys.stdout.write({body!r})\n'
            'sys.stdout.flush()\n'
            f'sys.stderr.write({stderr_body!r})\n'
            'sys.stderr.flush()\n'
            f'time.sleep({post_output_delay!r})\n'
            f'raise SystemExit({exit_code})\n'
        )
        cli.write_text(script)
        cli.chmod(0o700)
        return str(cli)

    def test_run_uses_noninteractive_json_cli_and_returns_real_exit_metadata(self):
        class Store:
            def get(self, selected):
                return {'task_id': selected, 'run_id': 'b' * 32}

        body = '\n'.join((
            self.event('step_start'),
            self.event('text', part={'text': 'fixture result'}),
            self.event('step_finish', part={'tokens': {'input': 5, 'output': 8}}),
        )) + '\n'
        result = self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                     prompt='bounded task prompt', timeout_seconds=5,
                     executable=self.fake_cli(body), dispatch_store=Store())
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.session_id, 'ses_Abc123')
        self.assertEqual(result.text, 'fixture result')
        self.assertEqual(result.usage['total_tokens'], 13)
        self.assertEqual(result.usage_source, 'CLI_REPORTED')
        self.assertEqual(result.cleanup_status, 'CONFIRMED')
        self.assertEqual(result.cli_version, 'opencode v2.0.32')
        self.assertEqual(result.source_bytes, len(b'VALUE = 1\n'))
        self.assertEqual(result.source_file_count, 1)
        self.assertEqual(len(result.source_manifest_sha256), 64)
        self.assertEqual(len(result.stdout_sha256), 64)
        self.assertEqual((result.task_id, result.run_id), ('a' * 32, 'b' * 32))
        self.assertLessEqual(datetime.fromisoformat(result.started_at.replace('Z', '+00:00')),
                             datetime.fromisoformat(result.ended_at.replace('Z', '+00:00')))

    def test_credential_shaped_read_path_source_is_allowed(self):
        source_text = 'OPENAI_' + 'API_' + 'KEY=' + 'sk-' + 'A' * 24 + '\n'
        (self.root / 'source.py').write_text(source_text)
        cli = self.fake_cli(self.event('step_start') + '\n')
        result = self.run_with_valid_binding(
            root=self.root, read_paths=['source.py'], write_paths=['source.py'],
            prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.source_bytes, len(source_text.encode('utf-8')))

    def test_non_utf8_source_is_rejected_before_cli_launch(self):
        (self.root / 'source.py').write_bytes(b'VALUE = \xff\n')
        marker = self.root / 'invoked'
        cli = self.fake_cli(self.event('step_start') + '\n', invocation_marker=marker)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(
                root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_SOURCE_INVALID')
        self.assertFalse(marker.exists())

    def test_credential_shaped_pm_prompt_is_rejected_before_cli_launch(self):
        prompt_text = '-----BEGIN ' + 'PRIVATE' + ' KEY-----\nfixture-only\n'
        marker = self.root / 'invoked'
        cli = self.fake_cli(self.event('step_start') + '\n', invocation_marker=marker)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(
                root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt=prompt_text, timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_PROMPT_SENSITIVE')
        self.assertFalse(marker.exists())

    def test_source_identity_drift_after_version_probe_blocks_provider_run(self):
        run_marker = self.root / 'run-invoked'
        cli = self.fake_cli(self.event('step_start') + '\n',
                            version_mutate_path=self.root / 'source.py',
                            run_invocation_marker=run_marker)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_SOURCE_CHANGED')
        self.assertFalse(run_marker.exists())

    def test_source_byte_limit_blocks_cli_launch(self):
        self.assertEqual(MAX_SOURCE_FILE_BYTES, 1024 * 1024)
        (self.root / 'source.py').write_text('x' * (MAX_SOURCE_FILE_BYTES + 1))
        marker = self.root / 'invoked'
        cli = self.fake_cli(self.event('step_start') + '\n', invocation_marker=marker)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_SOURCE_TOO_LARGE')
        self.assertFalse(marker.exists())

    def test_any_well_formed_version_runs_and_is_recorded(self):
        body = self.event('step_start') + '\n'
        for version in ('opencode v2.1.4', 'opencode v3.0.0-beta.1+build.7'):
            with self.subTest(version=version):
                cli = self.fake_cli(body, version_output=version + '\n')
                # #1776: a loose version-probe timeout, so a slow runner does not fail it.
                with patch('llmtool.opencode_direct_developer.VERSION_TIMEOUT_SECONDS', 120):
                    result = self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                        write_paths=['source.py'], prompt='bounded task prompt',
                        timeout_seconds=5, executable=cli)
                self.assertEqual(result.cli_version, version)

    def test_malformed_version_is_rejected_before_provider_run(self):
        marker = self.root / 'run-invoked'
        for version in ('opencode v2.1', 'opencode v2.1.4 (beta)', 'opencode v2.1.4\nextra',
                        'OpenCode 2.1.4', 'opencode v2.1.4-', 'opencode v\u0662.1.4'):
            with self.subTest(version=version):
                cli = self.fake_cli(self.event('step_start') + '\n',
                                    version_output=version + '\n',
                                    run_invocation_marker=marker)
                # #1776: a loose version-probe timeout, so a slow runner does not fail it.
                with patch('llmtool.opencode_direct_developer.VERSION_TIMEOUT_SECONDS', 120), \
                        self.assertRaises(DirectOpenCodeError) as caught:
                    self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                        write_paths=['source.py'], prompt='bounded task prompt',
                        timeout_seconds=5, executable=cli)
                self.assertEqual(caught.exception.direct_opencode_code,
                                 'OPENCODE_VERSION_UNSUPPORTED')
                self.assertFalse(marker.exists())

    def test_configured_free_model_is_passed_to_cli_and_config(self):
        model = 'opencode/other-model-free#high'
        cli = self.fake_cli(self.event('step_start') + '\n', model=model)
        result = self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
            write_paths=['source.py'], prompt='bounded task prompt',
            timeout_seconds=5, executable=cli, model=model)
        self.assertEqual(result.exit_code, 0)

    def test_default_variant_model_is_passed_to_cli_and_config(self):
        self.assertEqual(MODEL, 'opencode/deepseek-v4.1-flash-free#high')
        cli = self.fake_cli(self.event('step_start') + '\n')
        result = self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
            write_paths=['source.py'], prompt='bounded task prompt',
            timeout_seconds=5, executable=cli)
        self.assertEqual(result.exit_code, 0)

    def test_free_model_variants_are_accepted(self):
        for model in ('opencode/example-free', 'opencode/example-free#high'):
            with self.subTest(model=model):
                self.assertEqual(configuration(read_paths=['source.py'],
                    write_paths=['source.py'], model=model)['model'], model)

    def test_variant_is_stripped_from_provider_model_key(self):
        value = configuration(read_paths=['source.py'], write_paths=['source.py'],
                              model='opencode/x-free#high')
        self.assertEqual(value['providers'], {
            'opencode': {'models': {'x-free': {
                'limit': {'output': MAX_OUTPUT_TOKENS},
            }}}
        })

    def test_non_free_or_malformed_model_is_rejected_before_any_cli_launch(self):
        marker = self.root / 'invoked'
        cli = self.fake_cli(self.event('step_start') + '\n', invocation_marker=marker)
        for model in ('opencode/paid-model', 'other/name-free', 'opencode/a/b-free',
                      'opencode/-free', 'opencode/x-free\n', None,
                      'opencode/example-paid#high', 'opencode/example-free#',
                      'opencode/example-free#HIGH', 'opencode/example-free#a b',
                      'opencode/example-free#x#y',
                      'opencode/example-free#abcdefghijklmnopq'):
            with self.subTest(model=model):
                with self.assertRaises(DirectOpenCodeError) as caught:
                    self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                        write_paths=['source.py'], prompt='bounded task prompt',
                        timeout_seconds=5, executable=cli, model=model)
                self.assertEqual(caught.exception.direct_opencode_code,
                                 'OPENCODE_MODEL_INVALID')
        self.assertFalse(marker.exists())

    def test_version_probe_output_is_bounded(self):
        cli = self.fake_cli(self.event('step_start') + '\n',
                            version_output='x' * 5000)
        with patch('llmtool.opencode_direct_developer.VERSION_TIMEOUT_SECONDS', 120), \
                self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_VERSION_OUTPUT_TOO_LARGE')

    def test_prelaunch_d6_drift_rejects_before_developer_process(self):
        run_marker = self.root / 'run-invoked'
        cli = self.fake_cli(self.event('step_start') + '\n',
                            run_invocation_marker=run_marker)
        with self.assertRaises(DirectOpenCodeError) as caught:
            run(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', prelaunch_validate=lambda: False,
                dispatch_store=object(), dispatch_task_id='a' * 32,
                timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_D6_BINDING_CHANGED')
        self.assertFalse(caught.exception.attempted)
        self.assertFalse(run_marker.exists())

    @unittest.skipUnless(hasattr(os, 'fork'), 'requires POSIX process groups')
    def test_silent_descendant_is_killed_before_reaping_successful_leader(self):
        marker = self.root / 'run-descendant-finished'
        cli = self.fake_cli(self.event('step_start') + '\n',
                            run_descendant_marker=marker)
        result = self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                     prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(result.cleanup_status, 'CONFIRMED')
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(hasattr(os, 'fork'), 'requires POSIX process groups')
    def test_version_probe_kills_silent_descendant_with_owned_lifecycle(self):
        marker = self.root / 'version-descendant-finished'
        cli = self.fake_cli(self.event('step_start') + '\n',
                            version_descendant_marker=marker)
        result = self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                     prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(result.cleanup_status, 'CONFIRMED')
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_run_times_out_owned_process(self):
        cli = self.fake_cli(self.event('step_start') + '\n', delay=2)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', timeout_seconds=1, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_TIMEOUT')

    def test_keyboard_interrupt_reaps_owned_process_and_attaches_observed_facts(self):
        cli = self.fake_cli(self.event('step_start') + '\n', delay=5)
        from llmtool.providers.cli_lifecycle import CLIProcessLifecycle
        original_check = CLIProcessLifecycle.check
        run_checks = []

        def interrupt_after_launch(lifecycle):
            process_args = getattr(lifecycle.process, 'args', ())
            if isinstance(process_args, (list, tuple)) and 'run' in process_args:
                run_checks.append(1)
                raise KeyboardInterrupt()
            return original_check(lifecycle)

        with patch('llmtool.opencode_direct_developer.CLIProcessLifecycle.check',
                   autospec=True, side_effect=interrupt_after_launch), \
                self.assertRaises(KeyboardInterrupt) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                write_paths=['source.py'], prompt='bounded task prompt',
                timeout_seconds=5, executable=cli)
        self.assertEqual(run_checks, [1])
        observed = caught.exception._llmtool_direct_opencode_execution
        self.assertEqual(observed['cleanup_status'], 'CONFIRMED')
        self.assertIsInstance(observed['process_exit_code'], int)
        self.assertEqual(observed['source_bytes'], len(b'VALUE = 1\n'))
        self.assertEqual(observed['source_file_count'], 1)
        self.assertEqual(len(observed['source_manifest_sha256']), 64)

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX process signals')
    def test_sigint_and_sigterm_reap_owned_process_and_preserve_signal_semantics(self):
        # #1709: a runner started with nohup or & inherits SIGINT=SIG_IGN, so
        # Python never installs default_int_handler and the self-sent SIGINT
        # below would be ignored. Install it for this test body only and
        # restore whatever handler the runner had afterwards.
        previous_sigint = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        for sig, error_type, expected_code in (
                (signal.SIGINT, KeyboardInterrupt, None),
                (signal.SIGTERM, CLIHostTermination, 128 + signal.SIGTERM)):
            with self.subTest(signal=sig):
                marker = self.root / ('signal-' + str(sig.value))
                cli = self.fake_cli(self.event('step_start') + '\n', delay=10,
                                    run_invocation_marker=marker)
                def signal_when_child_starts():
                    deadline = time.monotonic() + 5
                    while not marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    if marker.exists():
                        os.kill(os.getpid(), sig)

                sender = threading.Thread(target=signal_when_child_starts, daemon=True)
                sender.start()
                with self.assertRaises(error_type) as caught:
                    self.run_with_valid_binding(root=self.root,
                        read_paths=['source.py'], write_paths=['source.py'],
                        prompt='bounded task prompt', timeout_seconds=15,
                        executable=cli)
                sender.join(timeout=2)
                self.assertFalse(sender.is_alive())
                observed = caught.exception
                if expected_code is not None:
                    self.assertEqual(observed.code, expected_code)
                facts = observed._llmtool_direct_opencode_execution
                self.assertEqual(facts['cleanup_status'], 'CONFIRMED')
                self.assertLess(facts['process_exit_code'], 0)
                child_pid = int(marker.read_text(encoding='ascii').splitlines()[0])
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX process signals')
    def test_sigint_signal_test_passes_when_runner_inherited_sigint_ignored(self):
        # #1709 regression: simulate a runner started with nohup or &, where
        # SIGINT is SIG_IGN, then run the real signal test in full (setUp,
        # body and cleanups). It must still pass and must leave the inherited
        # SIG_IGN disposition in place afterwards.
        previous_sigint = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        inner = type(self)(
            'test_sigint_and_sigterm_reap_owned_process_and_preserve_signal_semantics')
        result = unittest.TestResult()
        inner.run(result)
        problems = [text for _, text in result.failures + result.errors]
        self.assertEqual(problems, [])
        self.assertEqual(result.skipped, [])
        self.assertEqual(result.testsRun, 1)
        self.assertTrue(result.wasSuccessful())
        self.assertIs(signal.getsignal(signal.SIGINT), signal.SIG_IGN)

    def test_nonzero_exit_preserves_only_observed_bounded_execution_metadata(self):
        cli = self.fake_cli(self.event('step_start') + '\n', exit_code=7)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        error = caught.exception
        self.assertEqual(error.direct_opencode_code, 'OPENCODE_PROVIDER_FAILED')
        self.assertTrue(error.attempted)
        self.assertEqual(error.process_exit_code, 7)
        self.assertEqual(error.cleanup_status, 'CONFIRMED')
        self.assertEqual(len(error.stdout_sha256), 64)
        self.assertGreaterEqual(error.elapsed_ms, 0)
        self.assertEqual(error.task_id, 'a' * 32)
        self.assertIsNone(error.run_id)
        self.assertEqual(error.native_session_id, 'ses_Abc123')
        self.assertIsNotNone(error.started_at)
        self.assertIsNotNone(error.ended_at)

    def test_nonzero_stderr_truncation_has_distinct_code(self):
        cli = self.fake_cli(self.event('step_start') + '\n', exit_code=7,
                            stderr_body='Session.StepFailedError: Response still truncated')
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                write_paths=['source.py'], prompt='bounded task prompt',
                timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_OUTPUT_TRUNCATED')

    def test_deeply_nested_json_line_keeps_typed_outcome_and_facts(self):
        # Regression: a nesting depth that makes json.loads raise RecursionError
        # must not turn a typed outcome into an untyped adapter failure.
        body = '[' * 200000 + '\n'
        for exit_code, expected in ((0, 'OPENCODE_OUTPUT_INVALID'),
                                    (7, 'OPENCODE_PROVIDER_FAILED')):
            with self.subTest(exit_code=exit_code):
                cli = self.fake_cli(body, exit_code=exit_code)
                with self.assertRaises(DirectOpenCodeError) as caught:
                    self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                        write_paths=['source.py'], prompt='bounded task prompt',
                        timeout_seconds=5, executable=cli)
                error = caught.exception
                self.assertEqual(error.direct_opencode_code, expected)
                self.assertTrue(error.attempted)
                self.assertEqual(error.process_exit_code, exit_code)
                self.assertEqual(error.cleanup_status, 'CONFIRMED')
                self.assertEqual(len(error.stdout_sha256), 64)
                self.assertEqual(error.task_id, 'a' * 32)
                self.assertIsNone(error.native_session_id)
                self.assertIsNotNone(error.ended_at)

    def test_deeply_nested_json_line_does_not_replace_timeout_code(self):
        cli = self.fake_cli('[' * 200000 + '\n', post_output_delay=5)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                write_paths=['source.py'], prompt='bounded task prompt',
                timeout_seconds=1, executable=cli)
        error = caught.exception
        self.assertEqual(error.direct_opencode_code, 'OPENCODE_TIMEOUT')
        self.assertEqual(error.cleanup_status, 'CONFIRMED')
        self.assertEqual(len(error.stdout_sha256), 64)
        self.assertIsNone(error.native_session_id)

    def test_run_rejects_out_of_root_and_symlinked_paths(self):
        outside = self.root.parent / 'outside-source.py'
        outside.write_text('VALUE = 0\n')
        self.addCleanup(outside.unlink)
        (self.root / 'linked.py').symlink_to(outside)
        cli = self.fake_cli(self.event('step_start') + '\n')
        for path in ('../outside-source.py', 'linked.py'):
            with self.subTest(path=path), self.assertRaises(DirectOpenCodeError):
                self.run_with_valid_binding(root=self.root, read_paths=[path], write_paths=[path],
                    prompt='bounded task prompt', timeout_seconds=5, executable=cli)

    # #1815: the run bound is resolve_route_timeout('opencode', ...), not a fixed 600.
    def run_bound_seconds(self, **kwargs):
        """Run once; return the observed run-lifecycle deadline minus launch time."""
        from llmtool.providers.cli_lifecycle import CLIProcessLifecycle as RealLifecycle
        seen = []

        def lifecycle(name, *args, **kw):
            seen.append(kw['deadline'] - time.monotonic())
            return RealLifecycle(name, *args, **kw)

        cli = self.fake_cli(self.event('step_start') + '\n')
        with patch('llmtool.opencode_direct_developer.CLIProcessLifecycle',
                   side_effect=lifecycle):
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                write_paths=['source.py'], prompt='bounded task prompt',
                executable=cli, **kwargs)
        # The version probe comes first; the last lifecycle owns the run.
        self.assertGreaterEqual(len(seen), 2)
        return seen[-1] - 3  # the run lifecycle adds a 3-second cleanup margin

    def test_run_bound_defaults_to_route_default(self):
        bound = self.run_bound_seconds()
        self.assertGreater(bound, 590)
        self.assertLessEqual(bound, 600)

    def test_run_accepts_long_resolved_bound(self):
        for requested in (1200, 1800):
            with self.subTest(requested=requested):
                bound = self.run_bound_seconds(timeout_seconds=requested)
                self.assertGreater(bound, requested - 10)
                self.assertLessEqual(bound, requested)

    def test_run_refuses_bad_timeout_before_provider(self):
        cases = ((1801, 'TIMEOUT_OVER_HARD_CAP'), (3600, 'TIMEOUT_OVER_HARD_CAP'),
                 (0, 'TIMEOUT_INVALID'), (-1, 'TIMEOUT_INVALID'),
                 (True, 'TIMEOUT_INVALID'), (600.0, 'TIMEOUT_INVALID'),
                 ('600', 'TIMEOUT_INVALID'))
        for requested, code in cases:
            with self.subTest(requested=requested):
                marker = self.root / 'provider-run-invoked'
                cli = self.fake_cli(self.event('step_start') + '\n',
                                    invocation_marker=marker)
                with self.assertRaises(DirectOpenCodeError) as caught:
                    self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                        write_paths=['source.py'], prompt='bounded task prompt',
                        timeout_seconds=requested, executable=cli)
                self.assertEqual(caught.exception.direct_opencode_code, code)
                self.assertFalse(caught.exception.attempted)
                self.assertFalse(marker.exists())

    def test_resolved_timeout_matches_route_limits(self):
        from llmtool import opencode_direct_developer as adapter
        self.assertEqual(adapter.MAX_WALL_CLOCK_SECONDS, 1800)
        self.assertEqual(adapter.resolved_timeout(), 600)
        for requested in (1, 600, 1200, 1800):
            with self.subTest(requested=requested):
                self.assertEqual(adapter.resolved_timeout(requested), requested)
        with self.assertRaises(DirectOpenCodeError) as caught:
            adapter.resolved_timeout(1801)
        self.assertEqual(caught.exception.direct_opencode_code, 'TIMEOUT_OVER_HARD_CAP')
        with patch('llmtool.developer_routes.load_route_config',
                   return_value={'routes': {'opencode': {'default_timeout': 600,
                                                         'max_timeout': 600}}}):
            with self.assertRaises(DirectOpenCodeError) as caught:
                adapter.resolved_timeout(1200)
        self.assertEqual(caught.exception.direct_opencode_code, 'TIMEOUT_OVER_ROUTE_MAX')


if __name__ == '__main__':
    unittest.main()
