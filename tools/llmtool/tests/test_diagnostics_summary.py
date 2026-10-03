import json
import unittest
from dataclasses import replace

from llmtool import diagnostics as d
from llmtool.diagnostics_summary import RunSummary, parse_run_summary, strict_object


def sample_summary(**values):
    defaults = dict(run_id='a' * 32, mode=d.DiagnosticMode.NORMAL, status='success',
                    started_at='2026-10-03T00:00:00.000Z', ended_at='2026-10-03T00:00:00.001Z',
                    duration_ms=1, event_count=2, dropped_event_count=0, trace_bytes=500)
    defaults.update(values)
    return RunSummary(**defaults)


class SummaryTests(unittest.TestCase):
    def test_roundtrip_and_deterministic_json(self):
        value = sample_summary(provider_type='openai', final_error_code=d.ErrorCode.PROVIDER_TIMEOUT)
        self.assertEqual(parse_run_summary(value.to_json().encode()), value)
        self.assertEqual(value.to_json(), value.to_json())
        self.assertEqual(value.to_dict()['schema_version'], 1)
        self.assertTrue(value.persisted)

    def test_unknown_extension_ignored_missing_required_rejected(self):
        value = sample_summary().to_dict()
        value['future'] = {'data': 'ignored'}
        self.assertEqual(parse_run_summary(json.dumps(value).encode()), sample_summary())
        del value['trace_bytes']
        with self.assertRaises(ValueError):
            parse_run_summary(json.dumps(value).encode())

    def test_bad_scalars_and_privacy_fields_rejected(self):
        for key, value in (('run_id', '../escape'), ('command', 'SECRET_INPUT'),
                           ('provider_type', 'SECRET_RESPONSE'), ('status', 'bad'),
                           ('persisted', False), ('mode', 'normal'), ('event_count', True),
                           ('trace_bytes', 8388609), ('duration_ms', -1),
                           ('final_error_code', 'PROVIDER_TIMEOUT'), ('schema_version', True),
                           ('schema_version', 2), ('ended_at', '2026-10-02T00:00:00.000Z')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                replace(sample_summary(), **{key: value})

    def test_strict_json_bounds_duplicates_constants_utf8_depth(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e309}',
                    b'{"a":"control\\u001b"}', b'[]', b'\xff',
                    b'{' + b' ' * 4096 + b'}', b'{"a":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}'):
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                strict_object(raw, 4096)
