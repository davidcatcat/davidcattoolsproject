"""Deterministic C1 evidence fixtures; no provider or live-runtime checks."""

import base64
import hashlib
import io
import json
import os
import resource
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool.test_output_excerpts import (
    MAX_EXCERPT_BYTES, MAX_EXCERPTS, MAX_FAILURE_IDS, MAX_FILE_BYTES,
    MAX_SOURCE_LINES, MAX_TOTAL_EXCERPT_BYTES, select_test_excerpts,
)

# The parser child must stay within this CPU time (user plus system).
CHILD_CPU_BUDGET_SECONDS = 5.0
# Wall-clock limit for the parser child. It only stops a hang.
CHILD_HANG_TIMEOUT_SECONDS = 60


def unittest_capture(records, *, run_count=None, summary=None):
    parts = ['captured unittest output\n']
    for kind, identifier, body in records:
        parts.extend(['=' * 70 + '\n', kind + ': ' + identifier + '\n',
                      '-' * 70 + '\n', body + '\n\n'])
    count = Counter(kind for kind, _, _ in records)
    if summary is None:
        fields = []
        for kind, label in (('FAIL', 'failures'), ('ERROR', 'errors')):
            if count[kind]:
                fields.append(label + '=' + str(count[kind]))
        summary = 'FAILED (' + ', '.join(fields) + ')' if fields else 'OK'
    parts.extend(['-' * 70 + '\n', 'Ran ' + str(len(records) if run_count is None else run_count)
                  + ' tests in 0.012s\n\n', summary + '\n'])
    return ''.join(parts).encode('utf-8')


def pytest_capture(headers, counts):
    return ('============================= test session starts ==============================\n'
            'platform fixture -- Python fixture, pytest fixture\n'
            '=========================== short test summary info ============================\n'
            + ''.join(header + '\n' for header in headers)
            + '========================= ' + counts + ' in 0.03s =========================\n').encode('utf-8')


FAIL_ID = 'test_failure (sample.Case.test_failure)'
ERROR_ID = 'test_error (sample.Case.test_error)'
FAIL_BODY = 'Traceback (most recent call last):\n  File "tests/test_sample.py", line 8, in test_failure\n    self.fail("evidence")\nAssertionError: evidence'
ERROR_BODY = 'Traceback (most recent call last):\n  File "tests/test_sample.py", line 12, in test_error\n    raise ValueError("evidence")\nValueError: evidence'
UNIT_FAILURE = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY)])
# Synthetic, deterministic AWS-like shape (Issue #66 seed); never a real credential.
SYNTHETIC_AWS_KEY = 'AKIA' + 'SYNTHETIC0TEST16'
EMITTED_SENSITIVE_ERROR = ('test-excerpts: emitted excerpt contains sensitive-content '
                           'indicators; details omitted')


class TestOutputExcerptsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        # Product no-follow checks reject /tmp and /var aliases on macOS.
        self.root = Path(temp.name).resolve()
        self.path = self.root / 'capture.log'

    def select(self, raw=UNIT_FAILURE, exit_code=1, **bounds):
        self.path.write_bytes(raw)
        result = select_test_excerpts(self.path, exit_code, **bounds)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assert_provenance(result, raw)
        return result

    def assert_provenance(self, result, raw):
        source = result['source']
        digest = hashlib.sha256(raw).hexdigest()
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual(source, {'kind': 'captured_test_output', 'ref': 'sha256:' + digest,
                                  'sha256': digest, 'size_bytes': len(raw)})
        for failure in result['failure_sources']:
            anchor = failure['anchor']
            self.assertEqual(anchor['source_ref'], source['ref'])
            text = raw[anchor['start_byte']:anchor['end_byte']].decode().rstrip('\r\n')
            self.assertTrue(any(text == kind + ': ' + failure['id'] or
                                text.startswith(('FAILED ' if kind == 'FAIL' else 'ERROR ') + failure['id'])
                                for kind in failure['kinds']), text)
        used = 0
        for excerpt in result['excerpts']:
            self.assertEqual(excerpt['source_ref'], source['ref'])
            self.assertTrue(0 <= excerpt['start_byte'] < excerpt['end_byte'] <= len(raw))
            self.assertEqual(raw[excerpt['start_byte']:excerpt['end_byte']].decode(), excerpt['text'])
            self.assertLessEqual(excerpt['end_byte'] - excerpt['start_byte'], MAX_EXCERPT_BYTES)
            self.assertIn(excerpt['id'], result['failure_ids'])
            used += excerpt['end_byte'] - excerpt['start_byte']
        self.assertLessEqual(len(result['excerpts']), MAX_EXCERPTS)
        self.assertLessEqual(used, MAX_TOTAL_EXCERPT_BYTES)
        self.assertEqual(result['omitted']['source_bytes'], len(raw) - used)
        for summary in result['summary_sources']:
            self.assertEqual(summary['source_ref'], source['ref'])
            self.assertTrue(0 <= summary['start_byte'] < summary['end_byte'] <= len(raw))
        self.assertNotIn(str(self.path), json.dumps(result))

    def assert_incomplete(self, result, reason):
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertFalse(result['coverage']['failure_ids_complete'])
        self.assertTrue(result['coverage']['unknown'])
        self.assertIn(reason, result['coverage']['unknown_reasons'])

    def assert_rejected(self, raw):
        self.path.write_bytes(raw)
        with self.assertRaises(LLMToolError) as caught:
            select_test_excerpts(self.path, 1)
        self.assertNotIn(str(self.path), str(caught.exception))
        self.assertEqual(self.path.read_bytes(), raw)

    def select_with_deadline(self, raw):
        self.path.write_bytes(raw)
        environment = os.environ.copy()
        environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        # A child process lets the regression fail even if a regex holds the
        # GIL. Exercise the public parser, including physical reads.
        # The budget is the child's CPU time, so a busy host does not cause a
        # failure. The long wall-clock timeout only stops a hang.
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        completed = subprocess.run(
            [sys.executable, '-c',
             'import json, sys\n'
             'from llmtool.errors import LLMToolError\n'
             'from llmtool.test_output_excerpts import select_test_excerpts\n'
             'try:\n'
             '    result = select_test_excerpts(sys.argv[1], 1)\n'
             'except LLMToolError as error:\n'
             '    result = {"error": str(error)}\n'
             'print(json.dumps(result))\n', str(self.path)],
            env=environment, capture_output=True, text=True, check=True,
            timeout=CHILD_HANG_TIMEOUT_SECONDS)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        child_cpu = ((after.ru_utime - before.ru_utime)
                     + (after.ru_stime - before.ru_stime))
        self.assertLessEqual(
            child_cpu, CHILD_CPU_BUDGET_SECONDS,
            f'parser child used {child_cpu:.2f}s CPU, budget is '
            f'{CHILD_CPU_BUDGET_SECONDS}s')
        self.assertEqual(completed.stderr, '')
        self.assertEqual(self.path.read_bytes(), raw)
        return json.loads(completed.stdout)

    def near_limit_capture(self, prefix, unit, suffix=b''):
        size = MAX_FILE_BYTES - 1 - len(prefix) - len(suffix)
        raw = prefix + (unit * (size // len(unit) + 1))[:size] + suffix
        self.assertEqual(len(raw), MAX_FILE_BYTES - 1)
        return raw

    def assert_sensitive_with_deadline(self, raw):
        # The entire child stdout must decode to this fixed object, and the
        # deadline helper also requires empty stderr and an unchanged capture.
        self.assertEqual(self.select_with_deadline(raw), {
            'error': 'test-excerpts: source contains sensitive-content indicators; details omitted',
        })

    def assert_unknown_with_deadline(self, raw):
        result = self.select_with_deadline(raw)
        self.assert_provenance(result, raw)
        self.assert_incomplete(result, 'unknown_format')
        self.assertEqual(result['test_status'], 'FAILED')
        self.assertEqual(result['test_exit_code'], 1)
        self.assertEqual(result['failure_ids'], [])
        self.assertEqual(result['excerpts'], [])

    def test_unittest_failure_error_duplicate_ids_and_counts(self):
        raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY), ('ERROR', ERROR_ID, ERROR_BODY),
                                ('FAIL', FAIL_ID, FAIL_BODY.replace('evidence', 'second'))])
        result = self.select(raw)
        self.assertEqual(result['detected_format'], 'unittest')
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual(result['test_status'], 'FAILED')
        self.assertEqual(result['failure_ids'], [FAIL_ID, ERROR_ID])
        self.assertEqual(result['coverage']['reported_counts'], {'FAIL': 2, 'ERROR': 1})
        self.assertEqual(result['coverage']['observed_counts'], {'FAIL': 2, 'ERROR': 1})
        self.assertEqual(result['coverage']['run_count'], 3)
        self.assertEqual(result['failure_sources'][0]['occurrences'], 2)
        self.assertEqual(len(result['excerpts']), 3)

    def test_actual_stdlib_unittest_capture_matches_supported_headers(self):
        def failure(case):
            case.fail('captured fixture assertion')

        def error(case):
            raise ValueError('captured fixture error')

        # A local dynamic class avoids registering intentionally failing tests
        # with discovery while exercising this interpreter's real output.
        case_type = type('CapturedCase', (unittest.TestCase,),
                         {'test_failure': failure, 'test_error': error})
        capture = io.StringIO()
        suite = unittest.TestSuite([case_type('test_failure'), case_type('test_error'),
                                    case_type('test_failure')])
        run = unittest.TextTestRunner(stream=capture, verbosity=2).run(suite)
        self.assertFalse(run.wasSuccessful())
        result = self.select(capture.getvalue().encode('utf-8'), 1)
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual(result['coverage']['reported_counts'], {'FAIL': 2, 'ERROR': 1})
        self.assertEqual(result['coverage']['run_count'], 3)
        self.assertEqual(len(result['failure_ids']), 2)

    def test_unittest_chained_tracebacks_and_docstring(self):
        body = ERROR_BODY + '\n\nDuring handling of the above exception, another exception occurred:\n\n' + FAIL_BODY
        raw = unittest_capture([('FAIL', FAIL_ID, body)])
        raw = raw.replace((FAIL_ID + '\n').encode(), (FAIL_ID + '\nA test docstring\n').encode())
        result = self.select(raw)
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual(result['failure_ids'], [FAIL_ID])
        self.assertEqual(result['excerpts'][0]['text'].count('Traceback (most recent call last):'), 2)

    def test_pytest_mixed_outcomes_parametrized_ids_and_duplicate_order(self):
        node = 'tests/test_data.py::TestData::test_value[hello - 世界]'
        error = 'tests/test_setup.py::test_setup'
        raw = pytest_capture(['FAILED ' + node + ' - AssertionError: evidence',
                              'ERROR ' + error + ' - ValueError: evidence',
                              'FAILED ' + node + ' - AssertionError: repeated'], '2 failed, 1 error, 4 passed')
        result = self.select(raw)
        self.assertEqual(result['detected_format'], 'pytest')
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual(result['failure_ids'], [node, error])
        self.assertEqual(result['coverage']['reported_counts'], {'FAIL': 2, 'ERROR': 1})
        self.assertIsNone(result['coverage']['run_count'])
        self.assertEqual(result['failure_sources'][0]['occurrences'], 2)

    def test_pytest_collection_error_is_file_id_not_invented_node(self):
        result = self.select(pytest_capture(['ERROR tests/test_import.py - ImportError: evidence'], '1 error'), 2)
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual(result['test_exit_code'], 2)
        self.assertEqual(result['failure_ids'], ['tests/test_import.py'])

    def test_pytest_ambiguous_or_absolute_or_parent_node_is_unknown(self):
        for identifier in ('tests/test_p.py::test_p[a] - b] - AssertionError: evidence',
                           '/private/test_p.py::test_p', '../test_p.py::test_p',
                           'tests/test_p.py::test_p['):
            with self.subTest(identifier=identifier):
                result = self.select(pytest_capture(['FAILED ' + identifier], '1 failed'))
                self.assert_incomplete(result, 'malformed_failure_header')
                self.assertEqual(result['failure_ids'], [])

    def test_unicode_crlf_and_byte_offsets(self):
        raw = unittest_capture([('FAIL', 'test_測試 (sample.Case.test_測試)', FAIL_BODY + '\nUnicode 測試🙂')])
        raw = '前置🙂\n'.encode() + raw.replace(b'\n', b'\r\n')
        result = self.select(raw)
        self.assertEqual(result['status'], 'COMPLETE')
        first = result['failure_sources'][0]['anchor']['start_byte']
        self.assertEqual(first, raw.index('FAIL: test_測試'.encode()))
        self.assertIn('🙂', result['excerpts'][0]['text'])

    def test_truncation_is_utf8_aligned_and_not_a_coverage_claim(self):
        raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + '測試🙂' * 400)])
        unicode_start = raw.index('測試🙂'.encode())
        bound = unicode_start - raw.index(b'FAIL: ') + 1  # Cut inside a UTF-8 code point.
        result = self.select(raw, max_excerpt_bytes=bound)
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertTrue(result['excerpts'][0]['truncated'])
        self.assertEqual(result['omitted']['truncated_excerpts'], 1)
        self.assertGreater(result['omitted']['excerpt_bytes'], 0)
        self.assertLessEqual(len(result['excerpts'][0]['text'].encode()), bound)
        self.assertEqual(result['excerpts'][0]['end_byte'], unicode_start)

    def test_total_and_excerpt_count_limits_preserve_all_ids(self):
        records = [('FAIL', 'test_' + str(i) + ' (sample.Case.test_' + str(i) + ')',
                    FAIL_BODY + '\n' + 'evidence' * 400) for i in range(12)]
        raw = unittest_capture(records)
        result = self.select(raw)
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual(len(result['failure_ids']), 12)
        self.assertEqual(sum(len(item['text'].encode()) for item in result['excerpts']), MAX_TOTAL_EXCERPT_BYTES)
        self.assertEqual(result['omitted']['excerpts'], 4)
        result = self.select(raw, max_excerpts=2)
        self.assertEqual(len(result['excerpts']), 2)
        self.assertEqual(result['omitted']['excerpts'], 10)

    def test_id_limit_has_explicit_unknown_and_omitted_coverage(self):
        records = [('FAIL', 'test_' + str(i) + ' (sample.Case.test_' + str(i) + ')', FAIL_BODY)
                   for i in range(MAX_FAILURE_IDS + 1)]
        result = self.select(unittest_capture(records))
        self.assert_incomplete(result, 'failure_id_limit')
        self.assertEqual(len(result['failure_ids']), MAX_FAILURE_IDS)
        self.assertEqual(result['omitted']['failure_ids'], 1)

    def test_long_identifier_is_not_truncated_into_an_invented_id(self):
        node = 'tests/test_a.py::test_a[' + 'a' * 512 + ']'
        result = self.select(pytest_capture(['FAILED ' + node], '1 failed'))
        self.assert_incomplete(result, 'malformed_failure_header')
        self.assertEqual(result['failure_ids'], [])

    def test_complete_zero_failure_reports_do_not_claim_test_pass(self):
        for raw in (unittest_capture([], run_count=2), pytest_capture([], '2 passed'),
                    unittest_capture([], run_count=2, summary='OK (skipped=2)')):
            with self.subTest(raw=raw):
                result = self.select(raw, 0)
                self.assertEqual(result['status'], 'COMPLETE')
                self.assertEqual(result['test_status'], 'NO_FAILURES_REPORTED')
                self.assertEqual(result['failure_ids'], [])
                self.assertEqual(result['excerpts'], [])

    def test_nonzero_with_no_ids_and_zero_failure_summary_never_passes(self):
        for raw in (b'opaque build failure\n', unittest_capture([], run_count=4),
                    pytest_capture([], '4 passed'), b''):
            with self.subTest(raw=raw):
                result = self.select(raw, 7)
                self.assert_incomplete(result, 'nonzero_exit_without_failure_ids')
                self.assertEqual(result['test_exit_code'], 7)
                self.assertEqual(result['test_status'], 'FAILED')

    def test_zero_exit_with_failure_headers_is_inconsistent_not_success(self):
        for raw in (UNIT_FAILURE, pytest_capture(['FAILED tests/test_a.py::test_a'], '1 failed')):
            result = self.select(raw, 0)
            self.assert_incomplete(result, 'exit_summary_mismatch')
            self.assertEqual(result['test_status'], 'FAILED')
            self.assertEqual(result['test_exit_code'], 0)

    def test_negative_captured_signal_is_retained(self):
        result = self.select(UNIT_FAILURE, -15)
        self.assertEqual(result['test_exit_code'], -15)
        self.assertEqual(result['test_status'], 'FAILED')

    def test_run_count_and_summary_failure_mismatches(self):
        for raw, reason in ((unittest_capture([('FAIL', FAIL_ID, FAIL_BODY)], run_count=0), 'run_count_mismatch'),
                            (unittest_capture([('FAIL', FAIL_ID, FAIL_BODY)], summary='FAILED (failures=2)'), 'failure_count_mismatch'),
                            (pytest_capture(['FAILED tests/test_a.py::test_a'], '2 failed'), 'failure_count_mismatch')):
            result = self.select(raw)
            self.assert_incomplete(result, reason)

    def test_missing_summaries_truncated_body_and_nonterminal_summaries(self):
        cases = [(UNIT_FAILURE.split(b'Ran ')[0], 'missing_summary'),
                 (unittest_capture([('FAIL', FAIL_ID, 'Traceback (most recent call last):\n  unfinished')]), 'incomplete_failure_block'),
                 (UNIT_FAILURE + b'runner aborted later\n', 'nonterminal_summary'),
                 (UNIT_FAILURE[:-1], 'unterminated_source'),
                 (pytest_capture(['FAILED tests/test_a.py::test_a'], '1 failed').rsplit(b'=========================', 2)[0], 'missing_summary')]
        for raw, reason in cases:
            with self.subTest(reason=reason):
                result = self.select(raw)
                self.assert_incomplete(result, reason)

    def test_malformed_and_unsupported_outcomes_fail_closed(self):
        for raw in (unittest_capture([], summary='FAILED (failures=0)'),
                    unittest_capture([], run_count=1, summary='FAILED (unexpected successes=1)'),
                    unittest_capture([], summary='FAILED (failures=bogus)'),
                    unittest_capture([], summary='OK (errors=1)'),
                    pytest_capture([], '0 failed, 0 errors'),
                    pytest_capture([], '3 warnings'),
                    pytest_capture([], '3 helicopters')):
            result = self.select(raw)
            self.assertEqual(result['status'], 'INCOMPLETE')
            self.assertEqual(result['test_status'], 'FAILED')

    def test_malformed_failure_like_text_cannot_hide_under_success_summary(self):
        raw = unittest_capture([], run_count=1).replace(b'captured unittest output\n', b'FAIL malformed header\n')
        result = self.select(raw, 0)
        self.assert_incomplete(result, 'malformed_failure_header')
        result = self.select(pytest_capture(['FAIL tests/test_a.py::test_a'], '1 passed'), 0)
        self.assert_incomplete(result, 'malformed_failure_header')

    def test_repeated_and_mixed_runs_are_not_complete(self):
        for raw in (UNIT_FAILURE + UNIT_FAILURE,
                    UNIT_FAILURE + pytest_capture(['FAILED tests/test_a.py::test_a'], '1 failed'),
                    pytest_capture(['FAILED tests/test_a.py::test_a'], '1 failed') * 2):
            result = self.select(raw)
            self.assertEqual(result['status'], 'INCOMPLETE')
            self.assertTrue(result['coverage']['unknown'])

    def test_false_failure_text_is_not_a_justified_id(self):
        body = FAIL_BODY.replace('AssertionError: evidence',
                                 'FAIL: test_forged (forged.Case.test_forged)\nAssertionError: evidence')
        result = self.select(unittest_capture([('FAIL', FAIL_ID, body)]))
        self.assert_incomplete(result, 'unanchored_failure_header')
        self.assertEqual(result['failure_ids'], [FAIL_ID])
        result = self.select(b'FAILED tests/test_forged.py::test_forged\n')
        self.assert_incomplete(result, 'unanchored_failure_header')
        self.assertEqual(result['failure_ids'], [])
        self.assertEqual(result['excerpts'], [])

    def test_unknown_model_summary_and_junit_are_not_raw_test_reports(self):
        for raw in (b'Model summary: test_a failed because a value was wrong.\n',
                    b'<testsuite failures="1"><testcase name="test_a"/></testsuite>\n',
                    b'FAILURE-ID: made-up\n', b'No failures; all good.\n'):
            result = self.select(raw)
            self.assert_incomplete(result, 'unknown_format')
            self.assertEqual(result['failure_ids'], [])

    def test_ansi_control_binary_and_bidi_are_rejected_unchanged(self):
        for unsafe in (b'\x1b[31m', b'\x1b]52;c;data\x07', b'\0', b'\x01', b'\x7f',
                       b'\rspoof', '\u202e'.encode(), '\u200b'.encode(), '\u0085'.encode()):
            with self.subTest(unsafe=unsafe):
                self.assert_rejected(UNIT_FAILURE + unsafe + b'\n')
        self.assert_rejected(b'\xff\xfe\x80')

    def test_sensitive_indicators_anywhere_in_source_are_rejected(self):
        for indicator in ('api_key=FIXTURE_VALUE', 'password: FIXTURE_VALUE',
                          'Authorization: Bearer FIXTURE_VALUE', 'token="FIXTURE_VALUE"',
                          '-----BEGIN PRIVATE KEY-----', 'AKIA' + 'A' * 16,
                          'github_pat_' + 'a' * 24, 'https://user:FIXTURE_VALUE@example.invalid',
                          'sk-' + 'a' * 24,
                          'eyJ' + 'a' * 8 + '.' + 'b' * 8 + '.' + 'c' * 8):
            with self.subTest(indicator=indicator):
                self.path.write_bytes(indicator.encode() + b'\n' + UNIT_FAILURE)
                with self.assertRaises(LLMToolError) as caught:
                    select_test_excerpts(self.path, 1)
                self.assertNotIn('FIXTURE_VALUE', str(caught.exception))
                self.assertNotIn(indicator, str(caught.exception))
                self.assertNotIn(str(self.path), str(caught.exception))

    def assert_emitted_excerpt_rejected(self, raw, **bounds):
        self.path.write_bytes(raw)
        with self.assertRaises(LLMToolError) as caught:
            select_test_excerpts(self.path, 1, **bounds)
        self.assertEqual(str(caught.exception), EMITTED_SENSITIVE_ERROR)
        self.assertNotIn(str(self.path), str(caught.exception))
        self.assertEqual(self.path.read_bytes(), raw)

    def excerpt_bound(self, raw, value, extra=0):
        # Bytes from the failure header through the end of value, plus extra.
        return raw.index(value.encode()) + len(value.encode()) - raw.rindex(b'FAIL: ') + extra

    def test_byte_cut_word_boundary_cannot_emit_aws_like_indicator(self):
        # The source has no \b after the 16 key characters; a cut there adds one.
        raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + SYNTHETIC_AWS_KEY + 'MORE')])
        self.assertEqual(self.select(raw)['status'], 'COMPLETE')  # Full-source scan unchanged.
        self.assert_emitted_excerpt_rejected(
            raw, max_excerpt_bytes=self.excerpt_bound(raw, SYNTHETIC_AWS_KEY))

    def test_excerpt_cut_cannot_emit_aws_like_indicator(self):
        # synthetic-fixture: screen-tool test
        raw = unittest_capture([
            ('FAIL', FAIL_ID, FAIL_BODY + '\n' + SYNTHETIC_AWS_KEY + 'MORE測試')
        ])
        self.assertEqual(self.select(raw)['status'], 'COMPLETE')
        self.assert_emitted_excerpt_rejected(
            raw, max_excerpt_bytes=self.excerpt_bound(raw, SYNTHETIC_AWS_KEY))

    def test_jwt_ascii_continuation_is_rejected_before_excerpt_cut(self):
        # synthetic-fixture: screen-tool test
        jwt = 'eyJ' + 'a' * 8 + '.' + 'b' * 8 + '.' + 'c' * 8
        # MORE extends the JWT tail; the CJK boundary triggers source refusal.
        # synthetic-fixture: screen-tool test
        raw = unittest_capture([
            ('FAIL', FAIL_ID, FAIL_BODY + '\n' + jwt + 'MORE測試')
        ])
        self.path.write_bytes(raw)
        for bound in (MAX_EXCERPT_BYTES, self.excerpt_bound(raw, jwt)):
            with self.subTest(max_excerpt_bytes=bound):
                with self.assertRaises(LLMToolError) as caught:
                    select_test_excerpts(self.path, 1, max_excerpt_bytes=bound)
                self.assertEqual(
                    str(caught.exception),
                    'test-excerpts: source contains sensitive-content '
                    'indicators; details omitted')
                self.assertNotIn(str(self.path), str(caught.exception))
                self.assertEqual(self.path.read_bytes(), raw)

    def test_ascii_escaped_json_cannot_emit_aws_like_or_jwt_indicator(self):
        # A non-ASCII word suffix now triggers the intended source refusal before JSON emission.
        jwt = 'eyJ' + 'a' * 8 + '.' + 'b' * 8 + '.' + 'c' * 8
        for value in (SYNTHETIC_AWS_KEY, jwt):
            with self.subTest(value=value[:3]):
                raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + value + '測試')])
                self.path.write_bytes(raw)
                with self.assertRaises(LLMToolError) as caught:
                    select_test_excerpts(self.path, 1)
                self.assertEqual(str(caught.exception),
                                 'test-excerpts: source contains sensitive-content indicators; details omitted')
                self.assertEqual(self.path.read_bytes(), raw)

    def test_ascii_escaped_failure_id_without_excerpt_is_rejected(self):
        hidden = 'test_k (sample.' + SYNTHETIC_AWS_KEY + '測.test_k)'
        raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY), ('FAIL', hidden, FAIL_BODY)])
        self.path.write_bytes(raw)
        with self.assertRaises(LLMToolError) as caught:
            select_test_excerpts(self.path, 1, max_excerpts=1)
        self.assertEqual(str(caught.exception),
                         'test-excerpts: source contains sensitive-content indicators; details omitted')
        self.assertEqual(self.path.read_bytes(), raw)

    def test_near_miss_indicator_shapes_are_still_emitted(self):
        for value, extra in (('AKIA' + 'SYNTHETIC0TEST1', 0), ('AKIB' + 'SYNTHETIC0TEST16', 0),
                             ('AKIA' + 'SYNTHETIC0TEST1', 1)):
            with self.subTest(value=value, extra=extra):
                raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + value + '測試')])
                result = self.select(raw, max_excerpt_bytes=self.excerpt_bound(raw, value, extra))
                self.assertEqual(result['status'], 'COMPLETE')
                self.assertTrue(result['excerpts'][0]['text'].endswith(value))

    def test_aggregate_cut_cannot_emit_aws_like_indicator(self):
        per_excerpt = 2000
        fillers = [('FAIL', 'test_' + str(i) + ' (sample.Case.test_' + str(i) + ')',
                    FAIL_BODY + '\n' + 'evidence' * 300) for i in range(8)]
        remaining = MAX_TOTAL_EXCERPT_BYTES - 8 * per_excerpt
        head = ('FAIL: ' + FAIL_ID + '\n' + '-' * 70 + '\n' + FAIL_BODY + '\n').encode()
        padding = 'p' * (remaining - len(head) - len(SYNTHETIC_AWS_KEY) - 1)
        raw = unittest_capture(fillers + [('FAIL', FAIL_ID, FAIL_BODY + '\n' + padding + ' '
                                           + SYNTHETIC_AWS_KEY + 'MORE')])
        self.assertEqual(self.excerpt_bound(raw, SYNTHETIC_AWS_KEY), remaining)
        self.assertLess(remaining, per_excerpt)  # The aggregate cap, not the excerpt cap, cuts.
        self.assertEqual(self.select(raw)['status'], 'COMPLETE')
        self.assert_emitted_excerpt_rejected(raw, max_excerpt_bytes=per_excerpt)

    def test_truncated_innocuous_word_is_still_emitted(self):
        control = 'SYNTHETICCONTROL' + 'X' * 4
        raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + control + 'MORE')])
        result = self.select(raw, max_excerpt_bytes=self.excerpt_bound(raw, control))
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertTrue(result['excerpts'][0]['truncated'])
        self.assertTrue(result['excerpts'][0]['text'].endswith(control))

    def test_jwt_shapes_preserve_case_segments_and_word_boundaries(self):
        # Covers ASCII and Unicode JWT boundary behavior.
        # synthetic-fixture: screen-tool test
        jwt = 'eyJ' + 'a' * 8 + '.' + 'b' * 8 + '.' + 'c' * 8
        # synthetic-fixture: screen-tool test
        tokens = (jwt, jwt.upper(), 'prefix-' + jwt, 'prefix.' + jwt,
                  'eyJ-' * 6 + jwt, jwt + '-trailer', jwt + '.extra',
                  'eyJ' + '_' * 8 + '.' + '-' * 8 + '.' + '_' * 8,
                  'eyJ' + '\u0130' * 8 + '.' + '\u017f' * 8 + '.' + '\u212a' * 8,
                  'eyJ' + '\u0130' * 8 + '.' + '\u0131' * 8 + '.' + '\u017f' * 8 + '\u212a' * 8,
                  'eyJaaaaaaaa.bbbbbbbb.-------a',
                  'eyJaaaaaaaa.bbbbbbbb.aaaaaaa-測')
        for token in tokens:
            with self.subTest(token=token):
                raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + token)])
                self.assert_sensitive_with_deadline(raw)

    def test_non_jwt_shapes_preserve_supported_report_coverage(self):
        # synthetic-fixture: screen-tool test
        tokens = ('eyJaaaaaaa.bbbbbbbb.cccccccc',
                  'eyJaaaaaaaa.bbbbbbb.cccccccc',
                  'eyJaaaaaaaa.bbbbbbbb.ccccccc',
                  'eyJaaaaaaaa.bbbbbbbb.--------',
                  'eyJaaaaaaaa.bbbbbbbb.aaaaaaa-!',
                  'eyJaaaaaaaa..bbbbbbbb.cccccccc',
                  'eyJaaaaaaaa.bbbbbbbb',
                  'eyJaaaaaaaa/bbbbbbbb.cccccccc',
                  'a' + 'ey' + 'J' + 'a' * 8 + '.' + 'b' * 8 + '.' + 'c' * 8,
                  )
        # synthetic-fixture: screen-tool test
        cjk_jwt = '\u6e2c' + 'ey' + 'J' + 'a' * 8 + '.' + 'b' * 8 + '.' + 'c' * 8
        self.assert_rejected(cjk_jwt.encode() + b'\n' + UNIT_FAILURE)
        for token in tokens:
            with self.subTest(token=token):
                raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + token)])
                result = self.select(raw)
                self.assertEqual(result['status'], 'COMPLETE')
                self.assertEqual(result['test_status'], 'FAILED')
                self.assertEqual(result['failure_ids'], [FAIL_ID])

    def test_sensitive_values_directly_after_cjk_text_are_rejected(self):
        # synthetic-fixture: screen-tool test
        values = (
            'AK' + 'IA' + 'SYNTHETIC0TEST16',
            'sk' + '-' + 'a' * 24,
            'github' + '_pat_' + 'a' * 24,
            'xox' + 'b-' + 'a' * 20,
            'bear' + 'er' + ' ' + 'SYNTHETIC' + 'VALUE',
            'to' + 'ken' + '=' + 'SYNTHETIC' + 'VALUE',
        )
        for value in values:
            with self.subTest(shape=value[:3]):
                # synthetic-fixture: screen-tool test
                self.assert_rejected(('\u6e2c' + value).encode() + b'\n' + UNIT_FAILURE)

    def test_cloud_keys_and_jwts_between_cjk_letters_are_rejected(self):
        from llmtool.test_output_excerpts import (
            _emitted_json_sensitive, _emitted_sensitive, _sensitive,
            _sensitive_jwt,
        )
        from llmtool.high_debug import _screen_hits

        # This is a base64url encoded synthetic JWT shape.
        # synthetic-fixture: screen-tool test
        encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=').decode()
        # synthetic-fixture: screen-tool test
        jwt = (encode(b'{"alg":"HS256"}') + '.' +
               encode(b'{"sub":"1234567890"}') + '.signature123456')
        left_sides = ('\uff1a', '\u300c', '\uff0c', '\uff08', '\u3000',
                      '\u00a0', '\u0301', '', '\u0130', '\u0131', '\u017f',
                      '\u212a', '-', 'prefix-', 'x-')
        right_sides = ('\u6e2c', '\u3042', '\u00e9', '\u212a')
        # synthetic-fixture: screen-tool test
        side_pairs = (('\u6e2c', '\u3042'), ('\u3131', '\u3131'),
                      ('\uff71', '\uff71'), ('\u3005', '\u3005'),
                      ('\u6e2c', '\u00e9'), ('\u00e9', '\u6e2c'),
                      ('\u6e2c', '\u03b1'), ('\u03b1', '\u6e2c'),
                      ('\u6e2c', '\u0130'), ('\u6e2c', '\u0131'),
                      ('\u6e2c', '\u017f'), ('\u6e2c', '\u212a')) + tuple(
                      (left, right) for left in left_sides for right in right_sides)
        # synthetic-fixture: screen-tool test
        for value in (SYNTHETIC_AWS_KEY, 'ASIA' + 'SYNTHETIC0TEST16', jwt):
            for left, right in side_pairs:
                text = left + value + right
                with self.subTest(shape=value[:4], left=left, right=right):
                    self.assertTrue(_sensitive(text))
                    self.assertTrue(_emitted_sensitive(text))
                    self.assertTrue(_emitted_json_sensitive(text))
                    if value == jwt:
                        self.assertTrue(_sensitive_jwt(text))
                    self.assertTrue(_screen_hits(text))
                    self.assert_rejected(text.encode() + b'\n' + UNIT_FAILURE)

    def test_ascii_identifier_controls_remain_unchanged_for_screen_guards(self):
        from llmtool.test_output_excerpts import (
            _emitted_json_sensitive, _emitted_sensitive, _sensitive,
            _sensitive_jwt,
        )
        from llmtool.high_debug import _screen_hits

        # synthetic-fixture: screen-tool test
        jwt = 'eyJ' + 'a' * 8 + '.' + 'b' * 8 + '.' + 'c' * 8
        # synthetic-fixture: screen-tool test
        for value in (SYNTHETIC_AWS_KEY, 'ASIA' + 'SYNTHETIC0TEST16'):
            for text in ('x' + value, value + 'x', 'x' + value + '\u6e2c'):
                with self.subTest(shape=value[:4], text=text[:1]):
                    self.assertFalse(_sensitive(text))
                    self.assertFalse(_emitted_sensitive(text))
                    self.assertFalse(_emitted_json_sensitive(text))
                    self.assertFalse(_screen_hits(text))
        for text in ('x' + jwt, 'x' + jwt + '\u6e2c'):
            self.assertFalse(_sensitive(text))
            self.assertFalse(_sensitive_jwt(text))
            self.assertFalse(_emitted_sensitive(text))
            self.assertFalse(_emitted_json_sensitive(text))
            self.assertFalse(_screen_hits(text))
        # The existing ASCII suffix rule still recognizes a word boundary
        # inside a longer identifier as a JWT candidate.
        self.assertTrue(_sensitive(jwt + 'x'))
        self.assertTrue(_sensitive_jwt(jwt + 'x'))

    def test_diagnostics_cloud_key_screen_detects_cjk_neighbors_and_allows_longer_ascii_identifier(self):
        from llmtool import diagnostics as d

        # synthetic-fixture: screen-tool test
        cloud_key = 'AKIA' + '0' * 16
        # synthetic-fixture: screen-tool test
        cjk_wrapped = '北' + cloud_key + '京'
        with self.assertRaises(ValueError):
            d.safe_diagnostic_text(cjk_wrapped)
        identifier = 'prefix_' + cloud_key + '_suffix'
        self.assertEqual(d.safe_diagnostic_text(identifier), identifier)

    def test_remote_excerpt_cloud_key_boundary_matches_shared_rule(self):
        import ast
        import re
        from llmtool import remote_test
        from llmtool.test_output_excerpts import _CLOUD_KEY

        parsed = ast.parse(remote_test._REMOTE_SCRIPT)
        sensitive = next(node for node in parsed.body
                         if isinstance(node, ast.Assign) and
                         any(isinstance(target, ast.Name) and target.id == 'SENSITIVE'
                             for target in node.targets))
        helper = next(node for node in parsed.body
                      if isinstance(node, ast.FunctionDef) and
                      node.name == 'failure_excerpt')
        pattern = sensitive.value.args[0].value
        self.assertIn('|' + _CLOUD_KEY + '|', pattern)
        namespace = {'re': re, '_emitted_sensitive': remote_test._emitted_sensitive}
        exec(compile(ast.Module(body=[sensitive, helper], type_ignores=[]),
                     '<remote-excerpt-helper>', 'exec'), namespace)
        failure_excerpt = lambda output: namespace['failure_excerpt'](
            [output.encode(), b''], '/tmp/llmtool-remote-test.Abc123', '/home/runner')

        # synthetic-fixture: screen-tool test
        cloud_key = 'AKIA' + '0' * 16
        # synthetic-fixture: screen-tool test
        cjk_wrapped = '北' + cloud_key + '京'
        ascii_identifier = 'prefix_' + cloud_key + '_suffix'
        # synthetic-fixture: screen-tool test
        cjk_excerpt = failure_excerpt(
            'FAIL: test_expected (test_remote_test.Case)\n'
            'Traceback (most recent call last):\n'
            'AssertionError: ' + cjk_wrapped + '\n'
            '----------------------------------------------------------------------\n')
        self.assertNotIn(cloud_key, cjk_excerpt)
        identifier_excerpt = failure_excerpt(
            'FAIL: test_expected (test_remote_test.Case)\n'
            'Traceback (most recent call last):\n'
            'AssertionError: ' + ascii_identifier + '\n'
            '----------------------------------------------------------------------\n')
        self.assertIn('AssertionError: ' + ascii_identifier, identifier_excerpt)

    def test_test_run_summary_cloud_key_screen_detects_cjk_neighbors_and_allows_longer_ascii_identifier(self):
        import llmtool.test_run_summary as summary_module
        from llmtool.test_run_summary import summarize_test_run
        from llmtool.test_selection import select_test_plan

        head = 'e1b4ea2c85ff320d694168112da8fa531f126228'
        plan = select_test_plan(['tools/llmtool/llmtool/task_lifecycle.py'])
        ref = lambda value: 'sha256:' + hashlib.sha256(value).hexdigest()
        plan_digest = ref(json.dumps(plan, ensure_ascii=True, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('ascii'))
        commands = []
        for level in plan['levels']:
            commands_for_level = ([level['command']] if level['level'] == 'L0'
                                  else [item['argv'] for item in level.get('commands', [])])
            for argv in commands_for_level:
                log_digest = ref(b'synthetic caller-asserted log digest input')
                counts = ({'tests_run': 0, 'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 0}
                          if argv == ['make', 'syntax'] else
                          {'tests_run': 1, 'passed': 1, 'failed': 0, 'errors': 0, 'skipped': 0})
                commands.append({
                    'argv_sha256': ref(json.dumps(argv, ensure_ascii=True, sort_keys=True,
                                                  separators=(',', ':'), allow_nan=False).encode('ascii')),
                    'exit_status': 0,
                    'log_sha256': log_digest,
                    'log_ref': log_digest,
                    'counts': counts,
                    'failure_ids': [],
                })
        source = {'schema_version': 1, 'head_sha': head, 'plan_sha256': plan_digest,
                  'commands': commands}

        # synthetic-fixture: screen-tool test
        cloud_key = 'AKIA' + '0' * 16
        # synthetic-fixture: screen-tool test
        cjk_wrapped = '北' + cloud_key + '京'
        ascii_identifier = 'prefix_' + cloud_key + '_suffix'

        self.assertIsNotNone(summary_module._SENSITIVE_ID.search(cjk_wrapped))
        self.assertIsNone(summary_module._SENSITIVE_ID.search(ascii_identifier))

        source['commands'][1]['counts'] = {'tests_run': 1, 'passed': 0, 'failed': 1,
                                           'errors': 0, 'skipped': 0}
        source['commands'][1]['exit_status'] = 1
        source['commands'][1]['failure_ids'] = [cjk_wrapped]
        with self.assertRaises(ValueError):
            summarize_test_run(plan, source, expected_head=head)

        source['commands'][1]['failure_ids'] = [ascii_identifier]
        summary = summarize_test_run(plan, source, expected_head=head)
        self.assertEqual(summary['commands'][1]['failure_ids'], [ascii_identifier])

    def test_pm_runtime_cloud_key_screen_detects_cjk_neighbors_and_allows_longer_ascii_identifier(self):
        from llmtool import pm_runtime
        from llmtool.test_output_excerpts import _CLOUD_KEY

        self.assertIn(_CLOUD_KEY, pm_runtime._STREAM_SECRET_START.pattern)
        # synthetic-fixture: screen-tool test
        cloud_key = 'AKIA' + '0' * 16
        # synthetic-fixture: screen-tool test
        cjk_wrapped = '北' + cloud_key + '京'
        ascii_identifier = 'prefix_' + cloud_key + '_suffix'
        suffix_identifier = cloud_key + '_suffix'
        self.assertIsNotNone(pm_runtime._STREAM_SECRET_START.search(cjk_wrapped))
        self.assertEqual(pm_runtime._stream_safe_end(cjk_wrapped), 1)
        self.assertEqual(pm_runtime._screen_stream_text(cjk_wrapped), '北[REDACTED]京')
        self.assertIsNone(pm_runtime._STREAM_SECRET_START.search(ascii_identifier))
        self.assertEqual(pm_runtime._stream_safe_end(ascii_identifier), len(ascii_identifier))
        self.assertEqual(pm_runtime._screen_stream_text(ascii_identifier), ascii_identifier)
        self.assertIsNotNone(pm_runtime._STREAM_SECRET_START.search(suffix_identifier))
        self.assertEqual(pm_runtime._stream_safe_end(suffix_identifier), 0)
        self.assertEqual(pm_runtime._screen_stream_text(suffix_identifier), suffix_identifier)
        self.assertEqual(pm_runtime._stream_safe_end('北' + cloud_key[:8]), 1)
        self.assertEqual(pm_runtime._stream_safe_end('北' + cloud_key[:2]), 1)
        self.assertEqual(pm_runtime._stream_safe_end('prefix_' + cloud_key[:2]),
                         len('prefix_' + cloud_key[:2]))
        for deltas in (('北' + cloud_key[:8], cloud_key[8:] + '京'),
                       ('北' + cloud_key[:2], cloud_key[2:] + '京')):
            with self.subTest(first_delta=deltas[0]):
                record = {'turn_id': 'turn-1', 'result_text': '', 'tool_names': []}
                parser = pm_runtime._TurnParser(None, record, lambda: False)
                events = []
                for index, delta in enumerate(deltas):
                    parser.blocks[0] = parser.blocks.get(0, '') + delta
                    event = parser._flush_block(0, force=(index == len(deltas) - 1))
                    if event is not None:
                        events.append(event)
                self.assertEqual([event['type'] for event in events], ['TEXT_DELTA', 'TEXT_DELTA'])
                self.assertEqual([event['text'] for event in events], ['北', '[REDACTED]京'])
                for event in events:
                    self.assertNotIn('AK', event['text'])
                    self.assertNotIn('0', event['text'])
                self.assertEqual(record['result_text'], '北[REDACTED]京')

    def test_feedback_cloud_key_screen_detects_cjk_neighbors_and_allows_longer_ascii_identifier(self):
        from llmtool import feedback

        # synthetic-fixture: screen-tool test
        cloud_key = 'AKIA' + '0' * 16
        # synthetic-fixture: screen-tool test
        cjk_wrapped = '北' + cloud_key + '京'
        with self.assertRaises(LLMToolError):
            feedback._reject_credentials(cjk_wrapped)
        ascii_identifier = 'prefix_' + cloud_key + '_suffix'
        feedback._reject_credentials(ascii_identifier)

    def test_task_submission_cloud_key_screen_detects_cjk_neighbors_and_allows_longer_ascii_identifier(self):
        from llmtool import task_submission_cli
        from llmtool.test_output_excerpts import _CLOUD_KEY

        self.assertTrue(any(_CLOUD_KEY in pattern.pattern
                            for pattern in task_submission_cli._CAPTURE_SECRET_PATTERNS))
        # synthetic-fixture: screen-tool test
        cloud_key = 'AKIA' + '0' * 16
        # synthetic-fixture: screen-tool test
        cjk_wrapped = '北' + cloud_key + '京'
        ascii_identifier = 'prefix_' + cloud_key + '_suffix'
        self.assertTrue(any(pattern.search(cjk_wrapped)
                            for pattern in task_submission_cli._CAPTURE_SECRET_PATTERNS))
        self.assertEqual(task_submission_cli._redact_rejected_output(cjk_wrapped), '北[REDACTED]京')
        self.assertFalse(any(pattern.search(ascii_identifier)
                             for pattern in task_submission_cli._CAPTURE_SECRET_PATTERNS))
        self.assertEqual(task_submission_cli._redact_rejected_output(ascii_identifier), ascii_identifier)

    def test_near_limit_jwt_scan_completes_without_false_success(self):
        # Repeated boundary-bearing prefixes, missing dots, and short or
        # boundary-less segments must complete rather than time out or leak.
        cases = ((b'', b'eyJ-'), (b'', b'EyJ-'), (b'', b'eyJa'),
                 (b'', b'eyJ_'), (b'', b'eyJaaaaaaa.'),
                 (b'eyJaaaaaaaa.', b'eyJ-'),
                 (b'eyJaaaaaaaa.', b'aaaaaaa.'),
                 (b'eyJaaaaaaaa.aaaaaaaa.', b'-'))
        for prefix, unit in cases:
            with self.subTest(prefix=prefix, unit=unit):
                self.assert_unknown_with_deadline(self.near_limit_capture(prefix, unit))

    def test_near_limit_jwt_credentials_remain_rejected(self):
        # Exercise unbounded first, second and third segments, including a
        # tail whose valid word boundary precedes its final hyphen.
        cases = ((b'', b'eyJ-', b'.aaaaaaaa.aaaaaaaa\n'),
                 (b'eyJaaaaaaaa.', b'eyJ-', b'.aaaaaaaa\n'),
                 (b'eyJaaaaaaaa.aaaaaaaa.', b'-', b'a\n'),
                 (b'eyJaaaaaaaa.aaaaaaaa.', b'a-', b'!\n'))
        for prefix, unit, suffix in cases:
            with self.subTest(prefix=prefix, unit=unit):
                self.assert_sensitive_with_deadline(self.near_limit_capture(prefix, unit, suffix))

    def test_near_limit_other_sensitive_branches_have_explicit_oracles(self):
        # Fixed-width keys and prefix patterns without a required suffix
        # must not hide a timeout behind a generic rejection. Also cover the
        # unbounded PEM/whitespace runs with both missing and present suffixes.
        cases = (
            (b'-----BEGIN ', b'A', b'', False),
            (b'-----BEGIN ', b'A', b' PRIVATE KEY-----', True),
            (b'', b'AKIA' + b'A' * 15 + b'-', b'', False),
            (b'', b'AKIA' + b'A' * 15 + b'-', b'-AKIA' + b'A' * 16, True),
            (b'', b'sk-' + b'a' * 11 + b'!', b'', False),
            (b'sk-', b'a', b'', True),
            (b'', b'ghp_' + b'a' * 19 + b'!', b'', False),
            (b'github_pat_', b'a', b'', True),
            (b'', b'xoxb-' + b'a' * 19 + b'!', b'', False),
            (b'xoxb-', b'a', b'', True),
            (b'bearer', b' ', b'', False),
            (b'basic', b' ', b'FIXTURE_VALUE', True),
            (b'token', b' ', b'', False),
            (b'password', b' ', b'=', True),
        )
        for prefix, unit, suffix, sensitive in cases:
            with self.subTest(prefix=prefix, unit=unit, sensitive=sensitive):
                raw = self.near_limit_capture(prefix, unit, suffix)
                if sensitive:
                    self.assert_sensitive_with_deadline(raw)
                else:
                    self.assert_unknown_with_deadline(raw)

    def test_near_limit_url_scan_completes_without_false_success(self):
        cases = ((b'', b'a'), (b'', b'7'), (b'', b'+.-'), (b'', b'a7+.-'),
                 (b'a://', b'a'), (b'a://', b'a:'), (b'http://user:', b'a'),
                 (b'a://', b'@'))
        for prefix, unit in cases:
            with self.subTest(prefix=prefix, unit=unit):
                size = MAX_FILE_BYTES - len(prefix)
                raw = prefix + (unit * (size // len(unit) + 1))[:size]
                result = self.select_with_deadline(raw)
                self.assert_provenance(result, raw)
                self.assert_incomplete(result, 'unknown_format')
                self.assertEqual(result['test_status'], 'FAILED')
                self.assertEqual(result['test_exit_code'], 1)
                self.assertEqual(result['failure_ids'], [])
                self.assertEqual(result['excerpts'], [])

    def test_url_credentials_remain_rejected_without_component_length_caps(self):
        urls = ('https://user:FIXTURE_VALUE@example.invalid',
                'GIT+SSH://user:FIXTURE_VALUE@example.invalid',
                'a' * 1024 + '://user:FIXTURE_VALUE@example.invalid',
                'https://' + 'u' * 1024 + ':FIXTURE_VALUE@example.invalid',
                'https://user:' + 'p' * 1024 + '@example.invalid',
                'https://user@alias:FIXTURE_VALUE@example.invalid',
                'https://user:part:FIXTURE_VALUE@example.invalid',
                'https://user:part@alias@example.invalid',
                'a://prefix+a://user:FIXTURE_VALUE@example.invalid',
                '9+https://user:FIXTURE_VALUE@example.invalid',
                '\u0130://user:FIXTURE_VALUE@example.invalid')
        for url in urls:
            with self.subTest(url_length=len(url)):
                raw = url.encode() + b'\n' + UNIT_FAILURE
                result = self.select_with_deadline(raw)
                self.assertEqual(result, {'error': 'test-excerpts: source contains '
                                         'sensitive-content indicators; details omitted'})
                self.assertNotIn('FIXTURE_VALUE', json.dumps(result))
                self.assertNotIn(str(self.path), json.dumps(result))

        for prefix in (b'a' * 1024 + b'://', b'https://user:'):
            raw = prefix + b'u' * (MAX_FILE_BYTES - len(prefix) - 3) + b':p@'
            result = self.select_with_deadline(raw)
            self.assertEqual(result, {'error': 'test-excerpts: source contains '
                                     'sensitive-content indicators; details omitted'})

    def tab_escape_cases(self):
        # Fragments only: the CLI's JSON escape of TAB yields a "t" before "oken".
        value = 'SYNTHETIC_' + 'PRIVATE_VALUE'
        return value, [(separator, '\t' + 'oken' + separator + value)
                       for separator in (' = ', ': ')]

    def test_json_escaped_tab_cannot_print_a_credential_shaped_excerpt(self):
        value, cases = self.tab_escape_cases()
        for separator, tail in cases:
            with self.subTest(separator=separator):
                raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + tail)])
                self.assert_emitted_excerpt_rejected(raw)
                # A unittest description parenthetical may carry TAB into an emitted ID.
                hidden = 'test_k (sample.Case.test_k) (x' + tail + ')'
                raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY), ('FAIL', hidden, FAIL_BODY)])
                self.assert_emitted_excerpt_rejected(raw, max_excerpts=1)

    def test_json_escaped_tab_cli_prints_no_credential_shaped_text(self):
        value, cases = self.tab_escape_cases()
        for separator, tail in cases:
            with self.subTest(separator=separator):
                raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + tail)])
                self.path.write_bytes(raw)
                done = subprocess.run([sys.executable, '-B', '-I', '-m', 'llmtool',
                    'test-excerpts', '--file', str(self.path), '--exit-code', '1', '--json'],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
                    cwd=self.root, env={'PATH': os.defpath})
                self.assertEqual((done.returncode, done.stdout), (1, b''))
                self.assertEqual(done.stderr, ('llmtool: ' + EMITTED_SENSITIVE_ERROR
                                               + '\n').encode())
                for output in (done.stdout, done.stderr):
                    self.assertNotIn(value.encode(), output)
                    self.assertNotIn(b'oken', output)
                self.assertEqual(self.path.read_bytes(), raw)

    def test_harmless_tab_and_unicode_excerpts_still_complete(self):
        for text in ('harmless\tTAB text', 'expected\tvalue differs', 'tokenizer = ok',
                     'x\t期望值不同', '期望\t值不同', 'ok\ttokenizer'):
            with self.subTest(text=text):
                raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + text)])
                result = self.select(raw)
                self.assertEqual(result['status'], 'COMPLETE')
                self.assertEqual(result['test_status'], 'FAILED')
                self.assertEqual(result['failure_ids'], [FAIL_ID])
                self.assertIn(text, result['excerpts'][0]['text'])

    def test_urls_without_credentials_preserve_supported_report_coverage(self):
        for url in ('https://example.invalid:443/path',
                    'https://example.invalid/path:user@host',
                    'https://user@example.invalid',
                    'https://example.invalid:\nuser@host',
                    'https://example.invalid:\tuser@host'):
            with self.subTest(url=url):
                raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + url)])
                result = self.select(raw)
                self.assertEqual(result['status'], 'COMPLETE')
                self.assertEqual(result['test_status'], 'FAILED')
                self.assertEqual(result['failure_ids'], [FAIL_ID])

    def test_invalid_metadata_and_bounds_are_rejected_before_read(self):
        for exit_code in (True, False, '1', 1.0, None, -128, 256):
            with self.subTest(exit_code=exit_code), patch('llmtool.test_output_excerpts._read_file') as read:
                with self.assertRaises(LLMToolError):
                    select_test_excerpts(self.path, exit_code)
                read.assert_not_called()
        for bounds in ({'max_excerpt_bytes': 127}, {'max_excerpt_bytes': 2049},
                       {'max_excerpt_bytes': True}, {'max_excerpts': 0},
                       {'max_excerpts': 33}, {'max_excerpts': 1.5}):
            with self.subTest(bounds=bounds), patch('llmtool.test_output_excerpts._read_file') as read:
                with self.assertRaises(LLMToolError):
                    select_test_excerpts(self.path, 1, **bounds)
                read.assert_not_called()

    def test_oversized_or_excess_line_source_is_rejected(self):
        self.assert_rejected(b'x' * (MAX_FILE_BYTES + 1))
        self.assert_rejected(b'\n' * (MAX_SOURCE_LINES + 1))

    def test_symlink_directory_hardlink_and_fifo_are_rejected(self):
        self.path.write_bytes(UNIT_FAILURE)
        link = self.root / 'link.log'
        link.symlink_to(self.path)
        hard = self.root / 'hard.log'
        os.link(self.path, hard)
        fifo = self.root / 'pipe.log'
        os.mkfifo(fifo)
        for path in (self.root, link, hard, fifo):
            with self.subTest(path=path), self.assertRaises(LLMToolError):
                select_test_excerpts(path, 1)
        self.assertEqual(self.path.read_bytes(), UNIT_FAILURE)

    def test_parent_symlink_traversal_credential_and_git_paths_are_rejected(self):
        self.path.write_bytes(UNIT_FAILURE)
        directory = self.root / 'alias'
        directory.symlink_to(self.root, target_is_directory=True)
        for path in (directory / 'capture.log', str(self.root) + '/sub/../capture.log',
                     '', None, 'private\x1b.log', b'capture.log'):
            with self.subTest(path=path), self.assertRaises(LLMToolError):
                select_test_excerpts(path, 1)
        for name in ('.git', '.aws', '.ssh'):
            folder = self.root / name
            folder.mkdir()
            (folder / 'capture.log').write_bytes(UNIT_FAILURE)
            with self.assertRaises(LLMToolError):
                select_test_excerpts(folder / 'capture.log', 1)
        for name in ('.env', 'credentials.json', 'private.key'):
            path = self.root / name
            path.write_bytes(UNIT_FAILURE)
            with self.assertRaises(LLMToolError):
                select_test_excerpts(path, 1)

    def test_file_swap_during_open_is_rejected(self):
        self.path.write_bytes(UNIT_FAILURE)
        replacement = self.root / 'replacement.log'
        replacement.write_bytes(UNIT_FAILURE)
        real_open = os.open

        def swapped_open(name, flags, **kwargs):
            if name == self.path.name:
                replacement.replace(self.path)
            return real_open(name, flags, **kwargs)

        with patch('llmtool.test_output_excerpts.os.open', side_effect=swapped_open):
            with self.assertRaises(LLMToolError):
                select_test_excerpts(self.path, 1)

    def test_content_growth_rewrite_and_path_swap_during_read_are_rejected(self):
        real_read = os.read
        past = 1_000_000_000 * 10**9
        for mutation in ('grow', 'rewrite', 'swap'):
            self.path.write_bytes(UNIT_FAILURE)
            os.utime(self.path, ns=(past, past))
            replacement = self.root / 'replacement.log'
            replacement.write_bytes(UNIT_FAILURE)
            changed = []

            def changed_read(descriptor, size):
                raw = real_read(descriptor, size)
                if not changed:
                    changed.append(True)
                    if mutation == 'swap':
                        replacement.replace(self.path)
                    else:
                        self.path.write_bytes(UNIT_FAILURE + b'extra' if mutation == 'grow'
                                              else UNIT_FAILURE.replace(b'evidence', b'modified'))
                        if mutation == 'rewrite':
                            self.assertNotEqual(os.stat(self.path).st_mtime_ns, past)
                return raw

            with self.subTest(mutation=mutation), patch('llmtool.test_output_excerpts.os.read', side_effect=changed_read):
                with self.assertRaises(LLMToolError):
                    select_test_excerpts(self.path, 1)

    def test_parent_path_swap_after_read_is_rejected(self):
        folder = self.root / 'nested'
        folder.mkdir()
        path = folder / 'capture.log'
        path.write_bytes(UNIT_FAILURE)
        real_read = os.read
        changed = []

        def changed_read(descriptor, size):
            raw = real_read(descriptor, size)
            if not changed:
                changed.append(True)
                folder.rename(self.root / 'original')
                folder.mkdir()
                (folder / 'capture.log').write_bytes(UNIT_FAILURE)
            return raw

        with patch('llmtool.test_output_excerpts.os.read', side_effect=changed_read):
            with self.assertRaises(LLMToolError):
                select_test_excerpts(path, 1)

    def test_parent_directory_swap_during_open_is_rejected(self):
        folder = self.root / 'nested'
        folder.mkdir()
        path = folder / 'capture.log'
        path.write_bytes(UNIT_FAILURE)
        real_open = os.open

        def changed_open(name, flags, **kwargs):
            if name == 'nested':
                folder.rename(self.root / 'original')
                folder.mkdir()
                (folder / 'capture.log').write_bytes(UNIT_FAILURE)
            return real_open(name, flags, **kwargs)

        with patch('llmtool.test_output_excerpts.os.open', side_effect=changed_open):
            with self.assertRaises(LLMToolError):
                select_test_excerpts(path, 1)

    def test_short_reads_still_bind_full_source(self):
        self.path.write_bytes(UNIT_FAILURE)
        real_read = os.read
        with patch('llmtool.test_output_excerpts.os.read', side_effect=lambda fd, n: real_read(fd, min(n, 7))):
            result = select_test_excerpts(self.path, 1)
        self.assertEqual(result['status'], 'COMPLETE')
        self.assert_provenance(result, UNIT_FAILURE)

    def test_io_failures_are_sanitized_and_all_descriptors_close(self):
        self.path.write_bytes(UNIT_FAILURE)
        opened = []
        real_open = os.open

        def tracked_open(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            opened.append(fd)
            return fd

        with patch('llmtool.test_output_excerpts.os.open', side_effect=tracked_open), \
                patch('llmtool.test_output_excerpts.os.read', side_effect=OSError('PRIVATE_EXCEPTION_VALUE')):
            with self.assertRaises(LLMToolError) as caught:
                select_test_excerpts(self.path, 1)
        self.assertNotIn('PRIVATE_EXCEPTION_VALUE', str(caught.exception))
        self.assertTrue(opened)
        for descriptor in opened:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_offline_explicit_read_only_no_environment_or_subprocess(self):
        from llmtool import cli  # Load CLI wiring before prohibiting ambient I/O.
        self.path.write_bytes(UNIT_FAILURE)
        unrelated = self.root / 'unrelated.log'
        unrelated.write_bytes(b'UNSELECTED_PRIVATE_VALUE')
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        cwd = os.getcwd()
        with ExitStack() as stack:
            for name in ('subprocess.run', 'subprocess.Popen', 'socket.socket', 'os.scandir',
                         'os.listdir', 'os.getenv', 'os.chdir', 'builtins.open',
                         'llmtool.cli.resolve_config', 'llmtool.cli.api_key',
                         'llmtool.cli.create_client', 'llmtool.cli.collect_git_snapshot'):
                stack.enter_context(patch(name, side_effect=AssertionError('unexpected external operation')))
            result = select_test_excerpts(self.path, 1)
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertNotIn('UNSELECTED_PRIVATE_VALUE', json.dumps(result))
        self.assertEqual(os.getcwd(), cwd)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})
        self.assertLess(sum(len(item['text'].encode()) for item in result['excerpts']), len(UNIT_FAILURE))


    def actual_runner_capture(self, message='', verbosity=1):
        # Real stdlib run in our interpreter, not a formatted report imitation.
        program = '''import logging, sys, unittest
logging.basicConfig(stream=sys.stdout)
class ObservedCase(unittest.TestCase):
 def test_failure(self):
  if sys.argv[1]:
   logging.error(sys.argv[1])
   logging.error(sys.argv[1])
  self.fail('synthetic assertion evidence')
 def test_error(self):
  raise ValueError('synthetic error evidence')
suite = unittest.TestSuite([ObservedCase('test_failure'), ObservedCase('test_error'),
                            ObservedCase('test_failure')])
result = unittest.TextTestRunner(stream=sys.stdout, verbosity=int(sys.argv[2])).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
'''
        completed = subprocess.run([sys.executable, '-B', '-I', '-c', program,
                                   message, str(verbosity)], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=5, env={'PATH': os.defpath})
        self.assertEqual(completed.returncode, 1)
        self.assertLess(len(completed.stdout), 48 * 1024)
        self.assertNotIn(str(self.root).encode(), completed.stdout)
        return completed.stdout, completed.returncode

    def test_actual_default_unittest_root_logging_preserves_failure_coverage(self):
        raw, code = self.actual_runner_capture('synthetic observation')
        self.assertIn(b'\nERROR:root:synthetic observation\n', raw)
        result = self.select(raw, code)
        self.assertEqual(result['status'], 'COMPLETE')
        # unittest 3.9 renders Class, newer versions render Class.method.
        # Ask the framework for exact expected IDs instead of assuming a version.
        case_type = type('ObservedCase', (unittest.TestCase,), {
            '__module__': '__main__', 'test_error': lambda case: None,
            'test_failure': lambda case: None})
        self.assertEqual(result['failure_ids'], [str(case_type(name)) for name in
                         ('test_error', 'test_failure')])
        self.assertEqual(result['coverage']['reported_counts'], {'FAIL': 2, 'ERROR': 1})
        self.assertEqual(result['coverage']['observed_counts'], {'FAIL': 2, 'ERROR': 1})
        self.assertEqual(result['failure_sources'][1]['occurrences'], 2)
        self.assertEqual((result['test_exit_code'], result['test_status']), (1, 'FAILED'))
        # Caller-provided provenance is retained; parser never authenticates it.
        mismatched = self.select(raw, 0)
        self.assert_incomplete(mismatched, 'exit_summary_mismatch')
        self.assertEqual(mismatched['test_status'], 'FAILED')

    def test_curated_capture_quality_denominators_and_security_tradeoffs(self):
        actual = [('plain', '', 1, 'COMPLETE'),
                  ('root-log', 'synthetic observation', 1, 'COMPLETE'),
                  # Interrupted verbose progress leaves standalone FAIL/ERROR.
                  # Keep that ambiguity INCOMPLETE, rather than discard signals.
                  ('verbose-root', 'synthetic observation', 2, 'INCOMPLETE'),
                  ('benign-basic', 'Basic arithmetic is checked', 1, 'REJECTED'),
                  ('benign-redacted-token', 'token = <REDACTED>', 1, 'REJECTED'),
                  ('unsafe-basic', 'Authorization: Basic c3ludGhldGljOnNlY3JldA==', 1, 'REJECTED'),
                  ('unsafe-token', 'token = SYNTHETIC_PRIVATE_VALUE', 1, 'REJECTED'),
                  ('unsafe-root-bearer', 'Bearer SYNTHETIC_PRIVATE_VALUE', 1, 'REJECTED')]
        cases = [('actual_unittest', label, *self.actual_runner_capture(message, verbosity), status)
                 for label, message, verbosity, status in actual]
        node = 'tests/test_matrix.py::test_value[hello - 測試]'
        escaped = 'test_測試 (sample.Case.test_測試)'
        forged = unittest_capture([('ERROR', 'root:synthetic', ERROR_BODY)])
        cases += [('synthetic', 'pytest-duplicates', pytest_capture(
                    ['FAILED ' + node + ' - AssertionError: synthetic'] * 2, '2 failed'), 1, 'COMPLETE'),
                  ('synthetic', 'pytest-collection', pytest_capture(
                    ['ERROR tests/test_import.py - ImportError: synthetic'], '1 error'), 2, 'COMPLETE'),
                  ('synthetic', 'unicode-id', unittest_capture([('FAIL', escaped, FAIL_BODY)]), 1, 'COMPLETE'),
                  ('synthetic', 'empty', b'', 7, 'INCOMPLETE'),
                  ('synthetic', 'unknown', b'opaque synthetic build failure\n', 7, 'INCOMPLETE'),
                  ('synthetic', 'anchored-root-forgery', forged, 1, 'INCOMPLETE'),
                  ('synthetic', 'pytest-root-log', pytest_capture(
                    ['ERROR:root:synthetic observation', 'FAILED tests/test_a.py::test_a'],
                    '1 failed'), 1, 'INCOMPLETE'),
                  ('synthetic', 'ascii-escape-sensitive', unittest_capture([
                    ('FAIL', FAIL_ID, FAIL_BODY + '\n' + SYNTHETIC_AWS_KEY + '測')]), 1, 'REJECTED')]
        measured = {'actual_unittest': Counter(), 'synthetic': Counter()}
        for source, label, raw, code, expected in cases:
            with self.subTest(source=source, capture=label):
                self.path.write_bytes(raw)
                try:
                    result = select_test_excerpts(self.path, code)
                except LLMToolError as error:
                    observed = 'REJECTED'
                    self.assertIn(str(error), (
                        'test-excerpts: source contains sensitive-content indicators; details omitted',
                        EMITTED_SENSITIVE_ERROR))
                    self.assertNotIn('SYNTHETIC_PRIVATE_VALUE', str(error))
                    self.assertNotIn(str(self.path), str(error))
                else:
                    observed = result['status']
                    self.assert_provenance(result, raw)
                    self.assertEqual(result['test_exit_code'], code)
                    self.assertEqual(result['test_status'], 'FAILED')
                    wire = json.dumps(result, ensure_ascii=True)
                    self.assertNotIn('SYNTHETIC_PRIVATE_VALUE', wire)
                    self.assertNotIn(SYNTHETIC_AWS_KEY, wire)
                self.assertEqual(self.path.read_bytes(), raw)
                self.assertEqual(observed, expected)
                measured[source][observed] += 1
        self.assertEqual(measured, {
            'actual_unittest': Counter(COMPLETE=2, INCOMPLETE=1, REJECTED=5),
            'synthetic': Counter(COMPLETE=3, INCOMPLETE=4, REJECTED=1)})
        # Fixed, curated denominators; no production precision/recall claim.
        print('Issue186 curated quality ' + json.dumps(measured, sort_keys=True))

    def test_root_logging_does_not_hide_sensitive_original_or_emitted_forms(self):
        prefix = b'ERROR:root:synthetic observation\n'
        # synthetic-fixture: screen-tool test
        for tail, expected in ((SYNTHETIC_AWS_KEY, 'source'),
                               (SYNTHETIC_AWS_KEY + 'MORE測', 'emitted excerpt')):
            raw = prefix + unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + tail)])
            with self.subTest(form=expected):
                self.path.write_bytes(raw)
                with self.assertRaises(LLMToolError) as error:
                    select_test_excerpts(
                        self.path, 1,
                        **({'max_excerpt_bytes': self.excerpt_bound(raw, SYNTHETIC_AWS_KEY)}
                           if expected == 'emitted excerpt' else {}))
                self.assertEqual(str(error.exception), 'test-excerpts: ' + expected +
                                 ' contains sensitive-content indicators; details omitted')
                self.assertNotIn(SYNTHETIC_AWS_KEY, str(error.exception))
                self.assertEqual(self.path.read_bytes(), raw)

    def test_repeated_large_unicode_captures_keep_byte_provenance_and_deadline(self):
        # Constructed stress cases, distinct from the actual runner cohort.
        for size in (48 * 1024 + 17, MAX_FILE_BYTES - 1):
            for repetition in range(3):
                with self.subTest(size=size, repetition=repetition):
                    base = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY)])
                    pad = ('測試🙂' * (size // 10 + 1)).encode()[:size - len(base)]
                    pad = pad.decode('utf-8', errors='ignore').encode() + b'\n'
                    raw = pad + base
                    self.assertLessEqual(len(raw), MAX_FILE_BYTES)
                    result = self.select_with_deadline(raw)
                    self.assertEqual(result['status'], 'COMPLETE')
                    self.assertEqual(result['failure_ids'], [FAIL_ID])
                    self.assert_provenance(result, raw)

    @unittest.skipUnless(os.name == 'posix', 'requires real POSIX pipe closure')
    def test_real_closed_stdout_preserves_captured_failure_exit(self):
        raw, code = self.actual_runner_capture()
        self.path.write_bytes(raw)
        for exit_code in (code, 7, -15):
            read_fd, write_fd = os.pipe()
            os.close(read_fd)  # No reader exists before the child starts.
            try:
                done = subprocess.run([sys.executable, '-B', '-I', '-m', 'llmtool',
                    'test-excerpts', '--file', str(self.path), '--exit-code', str(exit_code), '--json'],
                    stdout=write_fd, stderr=subprocess.PIPE, timeout=5,
                    cwd=self.root, env={'PATH': os.defpath})
            finally:
                os.close(write_fd)
            self.assertEqual(done.returncode, exit_code if exit_code > 0 else 1, done.stderr)
            self.assertEqual(done.stderr, b'')
            self.assertEqual(self.path.read_bytes(), raw)

    def test_root_logging_cli_rejections_have_no_partial_json_or_private_echo(self):
        for tail, category in ((SYNTHETIC_AWS_KEY, 'source'),
                               (SYNTHETIC_AWS_KEY + '測', 'source')):
            raw = b'ERROR:root:synthetic observation\n' + unittest_capture([
                ('FAIL', FAIL_ID, FAIL_BODY + '\n' + tail)])
            self.path.write_bytes(raw)
            done = subprocess.run([sys.executable, '-B', '-I', '-m', 'llmtool',
                'test-excerpts', '--file', str(self.path), '--exit-code', '1', '--json'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
                cwd=self.root, env={'PATH': os.defpath})
            self.assertEqual((done.returncode, done.stdout), (1, b''))
            self.assertEqual(done.stderr, ('llmtool: test-excerpts: ' + category +
                ' contains sensitive-content indicators; details omitted\n').encode())
            self.assertNotIn(SYNTHETIC_AWS_KEY.encode(), done.stderr)
            self.assertNotIn(str(self.path).encode(), done.stderr)
            self.assertEqual(self.path.read_bytes(), raw)

    def test_near_limit_unicode_jwt_candidates_keep_bounded_oracles(self):
        for repetition in range(2):
            with self.subTest(repetition=repetition, outcome='reject'):
                prefix, suffix = b'eyJaaaaaaaa.', b'.aaaaaaaa\n'
                unit = 'İ'.encode('utf-8')  # IGNORECASE's non-ASCII JWT letter.
                raw = prefix + unit * ((MAX_FILE_BYTES - len(prefix) - len(suffix)) // len(unit)) + suffix
                self.assertGreater(len(raw), MAX_FILE_BYTES - 3)
                self.assert_sensitive_with_deadline(raw)
            with self.subTest(repetition=repetition, outcome='incomplete'):
                unit = '測eyJ-'.encode('utf-8')  # No word boundary before eyJ.
                raw = unit * ((MAX_FILE_BYTES - 1) // len(unit)) + b'\n'
                self.assertGreater(len(raw), MAX_FILE_BYTES - len(unit))
                self.assert_unknown_with_deadline(raw)

    def test_actual_pytest_when_already_available(self):
        import importlib.util
        if importlib.util.find_spec('pytest') is None:
            self.skipTest('pytest is not installed; no packages installed for this fixture')
        source = self.root / 'test_observed.py'
        source.write_text('def test_failure():\n    assert False, "synthetic assertion"\n'
                          'def test_error():\n    raise ValueError("synthetic error")\n')
        done = subprocess.run([sys.executable, '-B', '-I', '-m', 'pytest', '-q',
            '--tb=short', '--color=no', source.name], cwd=self.root,
            env={'PATH': os.defpath, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5)
        self.assertEqual(done.returncode, 1)
        result = self.select(done.stdout, done.returncode)
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual(result['failure_ids'], ['test_observed.py::test_failure',
                                               'test_observed.py::test_error'])


if __name__ == '__main__':
    unittest.main()
