"""Offline diagnostics foundation contracts; no live providers or store writers."""

import ast
import copy
import importlib
import io
import json
import pickle
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from llmtool import diagnostics as d
from llmtool.errors import LLMToolError, coded_error

MARKER = 'SUPER_SECRET_A11A1_MARKER'


def error(**changes):
    values = dict(code=d.ErrorCode.PROVIDER_TIMEOUT, message='gateway request timed out',
                  component='provider.openai-compatible')
    values.update(changes)
    return d.DiagnosticError(**values)


def event(**changes):
    values = dict(run_id='a' * 32, event_id=1, timestamp='2026-10-02T00:00:00.000Z',
                  type=d.EventType.ERROR, component='provider.openai',
                  severity=d.DiagnosticSeverity.ERROR,
                  data={'code': 'PROVIDER_TIMEOUT', 'message': 'gateway request timed out'})
    values.update(changes)
    if 'type' in changes:
        if 'severity' not in changes:
            if changes['type'] == d.EventType.WARNING:
                values['severity'] = d.DiagnosticSeverity.WARNING
            elif changes['type'] in (d.EventType.RUN_START, d.EventType.PROVIDER_REQUEST,
                                     d.EventType.PROVIDER_RESPONSE):
                values['severity'] = d.DiagnosticSeverity.INFO
        if 'data' not in changes and changes['type'] not in (d.EventType.ERROR, d.EventType.WARNING):
            values['data'] = {}
    return d.DiagnosticEvent(**values)


class PrivateObject:
    def __str__(self):
        raise AssertionError('private object was stringified: ' + MARKER)

    def __repr__(self):
        raise AssertionError('private object was represented: ' + MARKER)


class IdentityTests(unittest.TestCase):
    def test_run_id_generation_shape_and_uniqueness(self):
        values = {d.new_run_id() for _ in range(128)}
        self.assertEqual(len(values), 128)
        for value in values:
            self.assertRegex(value, r'^[0-9a-f]{32}$')

    def test_span_id_generation_shape_and_uniqueness(self):
        values = {d.new_span_id() for _ in range(128)}
        self.assertEqual(len(values), 128)
        for value in values:
            self.assertRegex(value, r'^[0-9a-f]{16}$')

    def test_generators_request_exact_random_byte_counts(self):
        with patch.object(d.secrets, 'token_hex', side_effect=['b' * 32, 'c' * 16]) as generate:
            self.assertEqual(d.new_run_id(), 'b' * 32)
            self.assertEqual(d.new_span_id(), 'c' * 16)
            self.assertEqual([call.args for call in generate.call_args_list], [(16,), (8,)])

    def test_invalid_ids_rejected_without_echo(self):
        for validate, width in ((d.validate_run_id, 32), (d.validate_span_id, 16)):
            for value in (None, 1, True, b'a' * width, '', 'A' * width, 'g' * width,
                          'a' * (width - 1), 'a' * (width + 1), 'a' * width + '\n', MARKER):
                with self.subTest(width=width, type=type(value)):
                    with self.assertRaises(ValueError) as caught:
                        validate(value)
                    self.assertNotIn(MARKER, str(caught.exception))

    def test_timestamp_utc_milliseconds_and_timezone_normalization(self):
        when = datetime(2026, 10, 2, 8, 1, 2, 345678, timezone(timedelta(hours=8)))
        self.assertEqual(d.utc_timestamp(when), '2026-10-02T00:01:02.345Z')
        self.assertRegex(d.validate_timestamp(d.utc_timestamp()), r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')
        self.assertEqual(d.utc_timestamp(datetime(1, 1, 1, tzinfo=timezone.utc)), '0001-01-01T00:00:00.000Z')

    def test_timestamp_rejects_naive_datetime_and_wrong_input(self):
        for value in (datetime(2026, 10, 2), 'now', True, PrivateObject()):
            with self.assertRaises(ValueError):
                d.utc_timestamp(value)

    def test_timestamp_requires_exact_format_and_real_date(self):
        for value in ('2026-10-02T00:00:00Z', '2026-10-02T00:00:00.000+00:00',
                      '2026-10-02T00:00:00.0000Z', '2026-02-30T00:00:00.000Z',
                      '2026-10-02T24:00:00.000Z', '2026-10-02T00:00:60.000Z',
                      '0000-01-01T00:00:00.000Z', '', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                d.validate_timestamp(value)

    def test_timestamp_rejects_iso_edge_times_before_calendar_parser(self):
        for value in ('2026-10-02T24:00:00.000Z', '2026-10-02T25:00:00.000Z',
                      '2026-10-02T23:60:00.000Z', '2026-10-02T23:59:60.000Z'):
            # The grammar must reject these even if a Python parser normalizes them.
            with self.subTest(value=value), patch.object(d, 'datetime') as parser:
                with self.assertRaises(ValueError):
                    d.validate_timestamp(value)
                parser.fromisoformat.assert_not_called()

    def test_timestamp_last_millisecond_and_leap_calendar(self):
        for value in ('2026-10-02T23:59:59.999Z', '2024-02-29T00:00:00.000Z'):
            self.assertEqual(d.validate_timestamp(value), value)
        for value in ('2026-02-29T00:00:00.000Z', '1900-02-29T00:00:00.000Z',
                      '2026-04-31T00:00:00.000Z', '2026-13-01T00:00:00.000Z'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                d.validate_timestamp(value)


class ErrorCompatibilityTests(unittest.TestCase):
    def test_provider_usage_is_detached_readonly_and_never_in_error_text(self):
        usage = {'prompt_tokens': 100, 'completion_tokens': 4096, 'total_tokens': 4196}
        exc = coded_error('safe original message', 'MODEL_OUTPUT_INCOMPLETE',
                          'provider.openai-compatible', provider_metadata=usage)
        usage['total_tokens'] = 999
        self.assertEqual(dict(exc.provider_metadata),
                         {'prompt_tokens': 100, 'completion_tokens': 4096, 'total_tokens': 4196})
        with self.assertRaises(TypeError):
            exc.provider_metadata['total_tokens'] = 999
        with self.assertRaises(AttributeError):
            exc.provider_metadata = {'total_tokens': 999}
        self.assertEqual(str(exc), 'safe original message')
        self.assertEqual(repr(exc), "LLMToolError('safe original message')")
        self.assertEqual(exc.diagnostic.code, d.ErrorCode.MODEL_OUTPUT_INCOMPLETE)
        self.assertNotIn('4196', exc.diagnostic.to_json())

    def test_provider_usage_validation_closed_keys_types_bounds_and_arithmetic(self):
        valid = {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5}
        invalid = (None, [], {}, {'prompt_tokens': 2}, dict(valid, raw_response=MARKER),
                   dict(valid, model=MARKER), dict(valid, prompt=MARKER),
                   {1: 2, 'completion_tokens': 3, 'total_tokens': 5})
        for item in invalid:
            with self.subTest(type=type(item)), self.assertRaises(ValueError):
                d.validate_provider_usage(item)
        for field in valid:
            for bad in (True, False, None, -1, d.MAX_TOKEN_COUNT + 1, 1.0, '1', MARKER, object()):
                with self.subTest(field=field, type=type(bad)), self.assertRaises(ValueError):
                    d.validate_provider_usage(dict(valid, **{field: bad}))
        with self.assertRaises(ValueError):
            d.validate_provider_usage(dict(valid, total_tokens=6))
        for item in ({'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                     {'prompt_tokens': d.MAX_TOKEN_COUNT, 'completion_tokens': 0,
                      'total_tokens': d.MAX_TOKEN_COUNT}):
            self.assertEqual(dict(d.validate_provider_usage(item)), item)

    def test_invalid_provider_usage_drops_without_altering_primary_error(self):
        for metadata in ({'total_tokens': True}, {'raw_body': MARKER}, object()):
            exc = coded_error('safe original message', 'MODEL_OUTPUT_INCOMPLETE',
                              'provider.openai-compatible', provider_metadata=metadata)
            self.assertIsNone(exc.provider_metadata)
            self.assertEqual(exc.diagnostic.code, d.ErrorCode.MODEL_OUTPUT_INCOMPLETE)
            self.assertEqual(exc.args, ('safe original message',))
            self.assertFalse(getattr(exc, 'diagnostic_dropped', False))
        with patch.object(d, 'validate_provider_usage', side_effect=ValueError(MARKER)):
            exc = coded_error('safe original message', 'MODEL_RESPONSE_INVALID',
                              'provider.openai-compatible', provider_metadata={})
        self.assertIsNone(exc.provider_metadata)
        self.assertEqual(exc.diagnostic.code, d.ErrorCode.MODEL_RESPONSE_INVALID)

    def test_provider_usage_independent_of_diagnostic_construction_failure(self):
        usage = {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3}
        with patch.object(d, 'DiagnosticError', side_effect=ValueError(MARKER)):
            exc = coded_error('safe original message', 'MODEL_OUTPUT_INCOMPLETE',
                              'provider.openai-compatible', provider_metadata=usage)
        self.assertTrue(exc.diagnostic_dropped)
        self.assertEqual(exc.diagnostic_code, d.ErrorCode.MODEL_OUTPUT_INCOMPLETE)
        self.assertEqual(dict(exc.provider_metadata), usage)
        self.assertEqual(str(exc), 'safe original message')

    def test_legacy_copy_and_coded_transfer_contract_unchanged(self):
        legacy = LLMToolError('safe legacy', 2)
        for cloned in (copy.deepcopy(legacy), pickle.loads(pickle.dumps(legacy))):
            self.assertEqual(cloned.args, legacy.args)
            self.assertIsNone(cloned.diagnostic)
            self.assertIsNone(cloned.provider_metadata)
        coded = coded_error('safe message', 'MODEL_OUTPUT_INCOMPLETE', 'provider.openai-compatible')
        for transform in (copy.deepcopy, pickle.dumps):
            with self.assertRaises(TypeError):
                transform(coded)

    def test_legacy_constructor_string_args_and_catching(self):
        for args in ((), ('gateway request timed out',), ('safe', 2)):
            with self.subTest(args=args):
                exc = LLMToolError(*args)
                self.assertEqual(exc.args, args)
                self.assertEqual(str(exc), str(Exception(*args)))
                self.assertIsNone(exc.diagnostic)
                with self.assertRaises(LLMToolError):
                    raise exc

    def test_legacy_pickle_retains_normal_exception_behavior(self):
        exc = pickle.loads(pickle.dumps(LLMToolError('safe legacy message')))
        self.assertEqual(exc.args, ('safe legacy message',))
        self.assertEqual(str(exc), 'safe legacy message')
        self.assertIsNone(exc.diagnostic)

    def test_coded_exception_retains_one_typed_value(self):
        diagnostic = error(retryable=True, safe_cause={'category': 'timeout'})
        exc = LLMToolError(diagnostic=diagnostic)
        self.assertIs(exc.diagnostic, diagnostic)
        self.assertEqual(str(exc), 'gateway request timed out')
        self.assertEqual(exc.args, ('gateway request timed out',))
        self.assertEqual(exc.diagnostic.safe_cause['category'], 'timeout')
        self.assertTrue(exc.diagnostic.retryable)

    def test_coded_exception_rejects_untyped_and_duplicate_message(self):
        for value in (PrivateObject(), {'code': 'PROVIDER_TIMEOUT'}, 'PROVIDER_TIMEOUT'):
            with self.assertRaises(TypeError) as caught:
                LLMToolError(diagnostic=value)
            self.assertNotIn(MARKER, str(caught.exception))
        with self.assertRaises(TypeError):
            LLMToolError('different message', diagnostic=error())

    def test_codes_are_closed_and_exactly_spec_set(self):
        self.assertEqual({code.value for code in d.ErrorCode}, {
            'USER_INPUT_ERROR', 'CONFIG_INVALID', 'CONFIG_PROVIDER_NOT_READY',
            'COMPANY_GW_DIRECT_RETIRED',
            'AUTH_CREDENTIAL_MISSING', 'AUTH_PROVIDER_REJECTED',
            'ENV_PROVIDER_RUNTIME_MISSING', 'NETWORK_PROVIDER_UNREACHABLE',
            'PROVIDER_TIMEOUT', 'PROVIDER_RATE_LIMITED', 'PROVIDER_HTTP_ERROR',
            'PROVIDER_RUNTIME_ERROR', 'MODEL_RESPONSE_INVALID', 'MODEL_OUTPUT_INCOMPLETE',
            'MODEL_RESPONSE_TOO_LARGE', 'LOCAL_IO_ERROR', 'INTERNAL_UNEXPECTED',
            'DIAGNOSTICS_STORE_UNAVAILABLE', 'DIAGNOSTICS_EVENT_DROPPED',
            'DIAGNOSTICS_STORE_UNSAFE', 'DIAGNOSTICS_STORE_FULL',
            'DIAGNOSTICS_RUN_COLLISION', 'DIAGNOSTICS_WRITE_FAILED',
        })
        for code in d.ErrorCode:
            self.assertEqual(error(code=code).code, code)
        for code in ('PROVIDER_TIMEOUT', 'TOOL_EXECUTION_FAILED', None, 1):
            with self.assertRaises(ValueError):
                error(code=code)

    def test_immutable_error_and_detached_cause(self):
        cause = {'http_status': 429}
        obj = error(safe_cause=cause)
        cause['http_status'] = 401
        self.assertEqual(obj.safe_cause['http_status'], 429)
        with self.assertRaises(FrozenInstanceError):
            obj.message = 'changed'
        with self.assertRaises(TypeError):
            obj.safe_cause['http_status'] = 401
        detached = obj.to_dict()
        detached['safe_cause']['http_status'] = 401
        self.assertEqual(obj.safe_cause['http_status'], 429)

    def test_error_json_deterministic_safe_and_flags_descriptive(self):
        a = error(safe_cause={'category': 'http', 'http_status': 429}, retryable=True)
        b = error(safe_cause={'http_status': 429, 'category': 'http'}, retryable=True)
        self.assertEqual(a.to_json(), b.to_json())
        self.assertEqual(json.loads(a.to_json())['code'], 'PROVIDER_TIMEOUT')
        self.assertEqual(json.loads(a.to_json())['fallback_allowed'], False)
        self.assertNotIn('\n', a.to_json())

    def test_flags_require_actual_bool_and_fallback_stays_false(self):
        for name in ('retryable', 'recoverable', 'fallback_allowed', 'user_action_required'):
            for value in (0, 1, None, 'false', []):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    error(**{name: value})
        with self.assertRaises(ValueError):
            error(fallback_allowed=True)

    def test_severity_and_mode_closed_enums(self):
        self.assertEqual({e.value for e in d.DiagnosticSeverity}, {'info', 'warning', 'error'})
        self.assertEqual({e.value for e in d.DiagnosticMode}, {'normal', 'verbose', 'debug'})
        for severity in ('error', 'critical', True, None):
            with self.assertRaises(ValueError):
                error(severity=severity)
        for invalid in ('trace', '', 1):
            with self.assertRaises(ValueError):
                d.DiagnosticMode(invalid)

    def test_error_message_utf8_bound(self):
        self.assertEqual(error(message='x' * d.ERROR_MESSAGE_MAX_BYTES).message, 'x' * 1024)
        error(message='é' * 512)
        for message in ('x' * 1025, 'é' * 513, '', ' \u3000', '\ud800'):
            with self.assertRaises(ValueError):
                error(message=message)

    def test_component_exact_ascii_syntax_and_bound(self):
        error(component='a' * d.IDENTIFIER_MAX_CHARS)
        for component in ('a' * 129, 'Provider.openai', '.provider', 'provider..http',
                          'provider/http', 'provider.\x1b', '中文', '', 'provider http'):
            with self.assertRaises(ValueError):
                error(component=component)

    def test_safe_cause_closed_scalars_ranges_and_bounds(self):
        obj = error(safe_cause={'http_status': 503, 'process_status': -9, 'category': 'runtime'})
        self.assertEqual(len(obj.safe_cause), 3)
        for cause in ({'http_status': True}, {'http_status': 99}, {'http_status': 600},
                      {'process_status': 2**32}, {'process_status': -(2**31)-1},
                      {'category': MARKER}, {'category': ['http']}, {'http_status': {'value': 429}},
                      {'url': 'https://secret.invalid'}, {'path': '/private/secret'},
                      {'stderr': MARKER}, {'response': MARKER}, {'environment': {}},
                      {'category': 'x' * 5000}, {1: 'timeout'}, None):
            with self.subTest(cause_type=type(cause)), self.assertRaises(ValueError) as caught:
                error(safe_cause=cause)
            self.assertNotIn(MARKER, str(caught.exception))
        with patch.object(d, 'SAFE_CAUSE_MAX_BYTES', 2), self.assertRaises(ValueError):
            error(safe_cause={'http_status': 429})

    def test_cause_rejects_nonfinite_and_arbitrary_values(self):
        for value in (float('nan'), float('inf'), float('-inf'), 1.0,
                      PrivateObject(), Exception(MARKER), Path(MARKER)):
            with self.assertRaises(ValueError) as caught:
                error(safe_cause={'process_status': value})
            self.assertNotIn(MARKER, str(caught.exception))


class SerializationAndPrivacyTests(unittest.TestCase):
    def test_json_plain_mapping_stable_order_ensure_ascii(self):
        self.assertEqual(d.diagnostic_json({'z': None, 'a': '中文'}),
                         '{"a":"\\u4e2d\\u6587","z":null}')
        self.assertEqual(d.diagnostic_json({'a': 'ok', 'z': 1}),
                         d.diagnostic_json({'z': 1, 'a': 'ok'}))

    def test_json_rejects_nonfinite_even_deeply(self):
        for value in (float('nan'), float('inf'), float('-inf'), 1.5):
            with self.assertRaises(ValueError):
                d.diagnostic_json({'safe_cause': {'http_status': value}})

    def test_json_rejects_objects_without_stringifying(self):
        for obj in (PrivateObject(), Exception(MARKER), Path(MARKER), set(), b'secret'):
            with self.assertRaises(ValueError) as caught:
                d.diagnostic_json({'message': obj})
            self.assertNotIn(MARKER, str(caught.exception))
        for obj in (PrivateObject(), Exception(MARKER)):
            with self.assertRaises(ValueError):
                d.diagnostic_json(obj)

    def test_json_rejects_non_plain_mapping_and_string_subclass(self):
        class MappingSubclass(dict):
            pass
        class StringSubclass(str):
            pass
        for obj in (MappingSubclass(a='b'), {'a': StringSubclass('b')}, {1: 'value'}):
            with self.assertRaises(ValueError):
                d.diagnostic_json(obj)

    def test_json_structural_bounds_and_cycles(self):
        cycle = {}; cycle['child'] = cycle
        for obj in (cycle, {'a': {'b': {'c': {'d': {'e': 1}}}}},
                    {str(i): i for i in range(65)}, {'items': [1, 2]}):
            with self.assertRaises(ValueError):
                d.diagnostic_json(obj)

    def test_json_event_size_boundary_counts_escaped_bytes(self):
        obj = {'message': '中文' * 300}
        encoded = d.diagnostic_json(obj)
        self.assertEqual(d.diagnostic_json(obj, len(encoded)), encoded)
        with self.assertRaises(ValueError):
            d.diagnostic_json(obj, len(encoded) - 1)
        for limit in (0, True, d.EVENT_MAX_BYTES + 1):
            with self.assertRaises(ValueError):
                d.diagnostic_json({}, limit)
        with self.assertRaises(ValueError):
            d.diagnostic_json({str(i): 'x' * 2048 for i in range(8)})

    def test_safe_text_rejects_controls_surrogates_and_empty_text(self):
        for text in ('a\x00b', 'a\x1bb', 'a\x7fb', 'a\x9bb', 'a\u202eb',
                     'a\nb', 'a\rb', 'a\tb', '\ud800', '', '  '):
            with self.assertRaises(ValueError):
                d.safe_diagnostic_text(text)

    def test_safe_text_preserves_normal_unicode(self):
        for text in ('中文\u3000內容', 'hello\u00a0world', '👩\u200d💻', 'a\u200cb'):
            self.assertEqual(d.safe_diagnostic_text(text), text)

    def test_safe_text_utf8_bound(self):
        d.safe_diagnostic_text('é' * 1024)
        for text in ('é' * 1025, 'x' * 2049):
            with self.assertRaises(ValueError):
                d.safe_diagnostic_text(text)

    def test_credentials_rejected_before_serialization(self):
        for text in ('sk-' + 'a' * 24, 'Authorization: Bearer ' + MARKER,
                     'Bearer ' + MARKER, 'api_key=' + MARKER, 'password: ' + MARKER,
                     'token=' + MARKER, 'AKIA' + 'A' * 16,
                     '-----BEGIN PRIVATE KEY-----', '"api-key":"' + MARKER + '"'):
            with self.subTest(prefix=text[:12]):
                for create in (lambda: error(message=text),
                               lambda: event(type=d.EventType.PROVIDER_RESPONSE, data={'model': text}),
                               lambda: d.diagnostic_json({'message': text})):
                    with self.assertRaises(ValueError) as caught:
                        create()
                    self.assertNotIn(MARKER, str(caught.exception))
                    self.assertNotIn(text, str(caught.exception))

    def test_credential_status_vocabulary_is_not_a_secret(self):
        values = ['provider authorization rejected', 'Authorization header missing',
                  'token=missing for profile', 'secret: not configured',
                  'Authorization: Bearer redacted', 'Authorization: Basic placeholder']
        for status in ('missing', 'unset', 'redacted', 'none', 'null',
                       'not configured', 'placeholder'):
            values.extend(('api_key="' + status + '"', 'password: ' + status,
                           "'token': '" + status + "'", 'secret=' + status.upper()))
        for text in values:
            with self.subTest(text=text):
                self.assertEqual(error(message=text).message, text)
                self.assertEqual(json.loads(d.diagnostic_json({'message': text}))['message'], text)

    def test_credential_values_still_rejected_with_fixed_safe_message(self):
        for text in ('Authorization: Basic dXNlcjpwYXNzd29yZA==',
                     'Authorization: Bearer ' + MARKER, 'Bearer ' + MARKER,
                     'api_key="' + MARKER + '"', "password='" + MARKER + "'",
                     'token=missing_' + MARKER, 'token=placeholder-' + MARKER,
                     'secret: not-' + MARKER, 'password=x',
                     '-----BEGIN RSA PRIVATE KEY-----'):
            with self.subTest(prefix=text[:12]):
                with self.assertRaises(ValueError) as caught:
                    error(message=text)
                self.assertEqual(str(caught.exception),
                                 'diagnostic text contains recognizable credential material')
                self.assertNotIn(MARKER, str(caught.exception))


class EventTests(unittest.TestCase):
    def test_event_exact_schema_and_stable_detached_json(self):
        obj = event(span_id='b' * 16, parent_span_id='c' * 16)
        data = json.loads(obj.to_json())
        self.assertEqual(set(data), {'schema', 'schema_version', 'run_id', 'event_id',
                                    'timestamp', 'type', 'component', 'severity', 'span_id',
                                    'parent_span_id', 'data'})
        self.assertEqual(data['schema'], 'llmtool.diagnostics.event')
        self.assertEqual(data['schema_version'], 1)
        self.assertEqual(obj.to_json(), obj.to_json())
        self.assertIsNone(event().to_dict()['span_id'])

    def test_event_schema_and_version_exact(self):
        for kwargs in ({'schema': 'other'}, {'schema': None}, {'schema_version': True},
                       {'schema_version': 0}, {'schema_version': 2}, {'schema_version': '1'}):
            with self.assertRaises(ValueError):
                event(**kwargs)

    def test_event_positive_integer_sequence_bound(self):
        for value in (0, -1, True, 1.0, '1', d.MAX_EVENT_ID + 1):
            with self.assertRaises(ValueError):
                event(event_id=value)
        event(event_id=d.MAX_EVENT_ID)

    def test_event_validates_run_span_and_parent(self):
        for kwargs in ({'run_id': 'x'}, {'span_id': 'a' * 32}, {'parent_span_id': 'x'},
                       {'parent_span_id': 'b' * 16},
                       {'span_id': 'b' * 16, 'parent_span_id': 'b' * 16}):
            with self.assertRaises(ValueError):
                event(**kwargs)

    def test_event_timestamp_severity_component_and_type(self):
        for kwargs in ({'timestamp': 'now'}, {'severity': 'error'}, {'component': 'host/path'},
                       {'type': 'error'}, {'type': 'retry'}, {'type': 'tool.exec'},
                       {'type': 'approval.granted'}):
            with self.assertRaises(ValueError):
                event(**kwargs)

    def test_run_start_closed_data_and_mode(self):
        obj = event(type=d.EventType.RUN_START, severity=d.DiagnosticSeverity.INFO,
                    data={'command': 'ask', 'mode': 'debug'})
        self.assertEqual(obj.data['mode'], 'debug')
        for data in ({'command': 'shell'}, {'mode': 'trace'}, {'cwd': MARKER}, {'mode': True}):
            with self.assertRaises(ValueError):
                event(type=d.EventType.RUN_START, data=data)

    def test_run_end_status_and_integer_duration(self):
        event(type=d.EventType.RUN_END, data={'status': 'success', 'duration_ms': 12})
        for data in ({'status': 'model-approved'}, {'duration_ms': -1},
                     {'duration_ms': True}, {'duration_ms': d.MAX_DURATION_MS + 1}):
            with self.assertRaises(ValueError):
                event(type=d.EventType.RUN_END, data=data)

    def test_provider_request_closed_selected_fields(self):
        event(type=d.EventType.PROVIDER_REQUEST,
              data={'provider_type': 'claude-cli', 'profile': 'local-claude', 'model': 'sonnet'})
        for data in ({'provider_type': 'anthropic'}, {'profile': '/tmp/profile'},
                     {'base_url': MARKER}, {'prompt': MARKER}, {'duration_ms': 1},
                     {'model': PrivateObject()}, {'model': 'x' * 129}):
            with self.assertRaises(ValueError):
                event(type=d.EventType.PROVIDER_REQUEST, data=data)

    def test_profile_names_match_existing_configuration_contract(self):
        event(type=d.EventType.PROVIDER_REQUEST, data={'profile': 'a' * 64})
        for value in ('a' * 65, 'profile.name', '-profile', 'profile/path'):
            with self.assertRaises(ValueError):
                event(type=d.EventType.PROVIDER_REQUEST, data={'profile': value})
        with self.assertRaises(ValueError):
            event(data={str(i): 0 for i in range(d.MAX_JSON_ITEMS + 1)})

    def test_model_identifiers_allow_known_provider_version_syntax(self):
        for model in ('claude-3-5-sonnet@20240620', 'qwen/Qwen3-32B', 'gpt-5',
                      'gpt-5.3-codex', 'codex-mini-latest', 'local-model:latest', 'a' * 128):
            with self.subTest(model=model):
                obj = event(type=d.EventType.PROVIDER_REQUEST, data={'model': model})
                self.assertEqual(obj.data['model'], model)

    def test_model_identifiers_reject_unsafe_values_and_traversal(self):
        for model in ('with space', 'model\tname', 'model\x1bname', 'a' * 129,
                      'sk-' + 'a' * 24, 'Bearer ' + MARKER, '../model',
                      'model/../version', 'model/./version', 'model/..'):
            with self.subTest(model_type=type(model)), self.assertRaises(ValueError):
                event(type=d.EventType.PROVIDER_REQUEST, data={'model': model})

    def test_error_and_warning_require_matching_severity_code_and_message(self):
        for kind, severity in ((d.EventType.ERROR, d.DiagnosticSeverity.ERROR),
                               (d.EventType.WARNING, d.DiagnosticSeverity.WARNING)):
            event(type=kind, severity=severity,
                  data={'code': 'PROVIDER_TIMEOUT', 'message': 'request timed out'})
            for other in d.DiagnosticSeverity:
                if other != severity:
                    with self.subTest(kind=kind, severity=other), self.assertRaises(ValueError):
                        event(type=kind, severity=other)
            for data in ({}, None, {'code': 'PROVIDER_TIMEOUT'}, {'message': 'safe'}):
                with self.subTest(kind=kind, data=data), self.assertRaises(ValueError):
                    event(type=kind, severity=severity, data=data)

    def test_start_and_provider_events_require_info(self):
        for kind in (d.EventType.RUN_START, d.EventType.PROVIDER_REQUEST, d.EventType.PROVIDER_RESPONSE):
            event(type=kind, severity=d.DiagnosticSeverity.INFO, data={})
            for severity in (d.DiagnosticSeverity.WARNING, d.DiagnosticSeverity.ERROR):
                with self.subTest(kind=kind, severity=severity), self.assertRaises(ValueError):
                    event(type=kind, severity=severity, data={})

    def test_run_end_severity_mapping_is_not_a_lifecycle_policy_yet(self):
        for severity in d.DiagnosticSeverity:
            event(type=d.EventType.RUN_END, severity=severity, data={'status': 'success'})

    def test_provider_response_integer_counts_and_null_tokens(self):
        data = {'provider_type': 'openai', 'input_tokens': 3, 'output_tokens': None,
                'total_tokens': None, 'token_usage_available': False, 'duration_ms': 2}
        obj = event(type=d.EventType.PROVIDER_RESPONSE, data=data)
        self.assertIsNone(json.loads(obj.to_json())['data']['output_tokens'])
        for data in ({'input_tokens': True}, {'input_tokens': -1},
                     {'input_tokens': d.MAX_TOKEN_COUNT + 1}, {'token_usage_available': 1},
                     {'stderr': MARKER}, {'response': PrivateObject()}):
            with self.assertRaises(ValueError):
                event(type=d.EventType.PROVIDER_RESPONSE, data=data)
        with patch.object(d, 'PROVIDER_METADATA_MAX_BYTES', 2), self.assertRaises(ValueError):
            event(type=d.EventType.PROVIDER_RESPONSE, data={'model': 'safe'})

    def test_error_warning_event_closed_nested_cause(self):
        data = {'code': 'PROVIDER_HTTP_ERROR', 'message': 'gateway rejected request',
                'safe_cause': {'http_status': 503}}
        for kind in (d.EventType.ERROR, d.EventType.WARNING):
            obj = event(type=kind, data=data)
            self.assertEqual(obj.to_dict()['data']['safe_cause']['http_status'], 503)
        for invalid in ({'message': 'missing code'}, {'code': 'UNSUPPORTED', 'message': 'safe'},
                        {'code': 'PROVIDER_TIMEOUT', 'message': MARKER, 'response': MARKER},
                        {'code': 'PROVIDER_TIMEOUT', 'message': 'safe', 'safe_cause': {'stderr': MARKER}}):
            with self.assertRaises(ValueError) as caught:
                event(data=invalid)
            self.assertNotIn(MARKER, str(caught.exception))

    def test_event_immutable_including_nested_mapping(self):
        data = {'code': 'PROVIDER_HTTP_ERROR', 'message': 'safe', 'safe_cause': {'http_status': 503}}
        obj = event(data=data)
        data['safe_cause']['http_status'] = 401
        self.assertEqual(obj.data['safe_cause']['http_status'], 503)
        with self.assertRaises(FrozenInstanceError):
            obj.event_id = 2
        with self.assertRaises(TypeError):
            obj.data['message'] = 'changed'
        with self.assertRaises(TypeError):
            obj.data['safe_cause']['http_status'] = 401
        detached = obj.to_dict(); detached['data']['safe_cause']['http_status'] = 401
        self.assertEqual(obj.data['safe_cause']['http_status'], 503)

    def test_event_control_arbitrary_nested_and_nonfinite_rejection(self):
        for data in ({'code': 'PROVIDER_TIMEOUT', 'message': 'bad\x1b'},
                     {'code': 'PROVIDER_TIMEOUT', 'message': 'safe', 'safe_cause': {'http_status': float('nan')}},
                     {'message': PrivateObject()}, {'unknown': {'nested': {'value': MARKER}}}):
            with self.assertRaises(ValueError):
                event(data=data)

    def test_event_size_enforced_without_truncating(self):
        obj = event(data={'code': 'INTERNAL_UNEXPECTED', 'message': 'safe'})
        with patch.object(d, 'EVENT_MAX_BYTES', len(obj.to_json()) - 1):
            with self.assertRaises(ValueError):
                obj.to_json()


def restore_module_after_reload(test, module):
    # A reload makes new objects for every name in the module. Other modules
    # that imported a name before keep the old object. Put every old attribute
    # back after the test, pass or fail, so those identities still hold.
    saved = dict(vars(module))

    def restore():
        namespace = vars(module)
        namespace.clear()
        namespace.update(saved)

    test.addCleanup(restore)


class SideEffectTests(unittest.TestCase):
    def test_import_and_model_use_perform_no_io_or_provider_calls(self):
        # Import machinery may read source; block data I/O, writes, network and
        # provider/executor seams while reloading and exercising the core itself.
        from llmtool import providers
        restore_module_after_reload(self, d)
        out, err = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            for target in ('builtins.open', 'os.open', 'os.mkdir', 'os.makedirs',
                           'os.remove', 'os.rename', 'os.system', 'socket.socket',
                           'socket.create_connection', 'subprocess.Popen', 'subprocess.run'):
                stack.enter_context(patch(target, side_effect=AssertionError('unexpected side effect')))
            create = stack.enter_context(patch.object(providers, 'create_client', side_effect=AssertionError('provider called')))
            stack.enter_context(redirect_stdout(out)); stack.enter_context(redirect_stderr(err))
            importlib.reload(d)
            diagnostic = error()
            self.assertEqual(str(LLMToolError(diagnostic=diagnostic)), diagnostic.message)
            event(run_id=d.new_run_id(), timestamp=d.utc_timestamp()).to_json()
            d.new_span_id()
            create.assert_not_called()
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), '')

    def test_reload_test_keeps_imported_screen_identity(self):
        # Run the reload test in this process, then check that a module which
        # imported a diagnostics name still sees the same object.
        from llmtool import diagnostics, telegram_channel
        original = diagnostics.safe_diagnostic_text
        self.assertIs(telegram_channel.safe_diagnostic_text, original)
        result = unittest.TestResult()
        SideEffectTests('test_import_and_model_use_perform_no_io_or_provider_calls').run(result)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        self.assertIs(diagnostics.safe_diagnostic_text, original)
        self.assertIs(telegram_channel.safe_diagnostic_text,
                      diagnostics.safe_diagnostic_text)

    def test_core_ast_has_no_execution_or_persistence_imports_calls(self):
        source = Path(d.__file__).read_text()
        tree = ast.parse(source)
        forbidden = {'subprocess', 'socket', 'http', 'urllib', 'os', 'pathlib', 'tempfile',
                     'providers', 'config', 'feedback_files', 'fcntl'}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [n.name for n in node.names] if isinstance(node, ast.Import) else [node.module or '']
                self.assertFalse(any(m.split('.')[0] in forbidden for m in modules), modules)
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
                self.assertNotIn(name, {'exec', 'eval', 'open', 'mkdir', 'makedirs', 'system',
                                        'Popen', 'write_text', 'write_bytes', 'create_client', 'complete'})
        self.assertNotIn('default=str', source.replace('Never use default=str.', ''))


if __name__ == '__main__':
    unittest.main()
