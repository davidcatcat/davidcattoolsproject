"""Deterministic C1 evidence fixtures; no provider or live-runtime checks."""

import hashlib
import io
import json
import os
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
        # A child process lets the regression fail promptly even if a regex
        # holds the GIL. Exercise the public parser, including physical reads.
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
            env=environment, capture_output=True, text=True, check=True, timeout=5)
        self.assertEqual(completed.stderr, '')
        self.assertEqual(self.path.read_bytes(), raw)
        return json.loads(completed.stdout)

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
                          'sk-' + 'a' * 24):
            with self.subTest(indicator=indicator):
                self.path.write_bytes(indicator.encode() + b'\n' + UNIT_FAILURE)
                with self.assertRaises(LLMToolError) as caught:
                    select_test_excerpts(self.path, 1)
                self.assertNotIn('FIXTURE_VALUE', str(caught.exception))
                self.assertNotIn(indicator, str(caught.exception))
                self.assertNotIn(str(self.path), str(caught.exception))

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
        for mutation in ('grow', 'rewrite', 'swap'):
            self.path.write_bytes(UNIT_FAILURE)
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


if __name__ == '__main__':
    unittest.main()
