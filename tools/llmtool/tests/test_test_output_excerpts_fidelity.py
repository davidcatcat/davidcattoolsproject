"""Issue #63 items 2/3: benign-capture measurement and capture fidelity.

Offline only: real captures come from a throwaway stdlib unittest file run in a
temporary directory; pytest captures are checked-in sanitized samples because
pytest is not a dependency. No provider, network, config or credential access.
Secret-shaped values are assembled from fragments at runtime.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from llmtool.errors import LLMToolError
from llmtool.test_output_excerpts import MAX_FILE_BYTES, select_test_excerpts

# Synthetic, fragment-built private marker; never a real credential.
PRIVATE = 'SYNTH' + 'ETIC_' + 'PRIVATE_' + 'VALUE'
SOURCE_REJECTED = 'test-excerpts: source contains sensitive-content indicators; details omitted'
EMITTED_REJECTED = 'test-excerpts: emitted excerpt contains sensitive-content indicators; details omitted'
# Generous wall-clock bound for one near-1 MiB scan in a child interpreter.
PERF_BOUND_SECONDS = 5.0
CHILD_TIMEOUT_SECONDS = 15

THROWAWAY_TESTS = r'''import logging, os, signal, sys, unittest
MODE = sys.argv.pop(1)

def emit():
    if MODE == 'root-log':
        logging.error('synthetic observation')
    elif MODE == 'module-log':
        logging.getLogger('app.db').error('synthetic retry observation')
    elif MODE == 'basic-auth':
        print('Basic auth disabled')
    elif MODE == 'token-none':
        print('token = None')
        print('password: null')
    elif MODE == 'repeated-48k':
        for index in range(1600):
            print('progress: synthetic step %04d ok' % index)
    elif MODE == 'flood':
        for index in range(40000):
            print('progress: synthetic step %06d ok' % index)
    elif MODE == 'unicode':
        print('期望值不同 🙂 İ K ı')
    elif MODE == 'sigterm':
        os.kill(os.getpid(), signal.SIGTERM)

class ThrowawayCase(unittest.TestCase):
    maxDiff = None
    def test_pass(self):
        self.assertTrue(True)
    def test_failure(self):
        emit()
        self.fail('synthetic assertion evidence')
    def test_error(self):
        raise ValueError('synthetic error evidence')
    def test_skip(self):
        self.skipTest('synthetic skip')
    @unittest.expectedFailure
    def test_xfail(self):
        self.fail('synthetic expected failure')
    def test_subtests(self):
        for index in range(3):
            with self.subTest(index=index):
                self.assertNotEqual(index, 1)
    def test_large_diff(self):
        self.assertEqual(list(range(7000)), list(range(1, 7001)))

names = ['ThrowawayCase.' + name for name in sys.argv[2:]]
verbosity = int(sys.argv[1])
if MODE == 'force-zero':
    unittest.main(argv=[sys.argv[0]] + names, verbosity=verbosity, exit=False)
    sys.exit(0)
logging.basicConfig()  # Default stderr handler and LEVEL:name:message format.
unittest.main(argv=[sys.argv[0]] + names, verbosity=verbosity)
'''


def pytest_sample(*, stdout=(), log=(), stderr=(), locals_=(), passed_only=False, extra_summary=''):
    """Sanitized pytest 8 default (-ra off) report layout; no real paths."""
    lines = ['============================= test session starts ==============================',
             'platform synthetic -- Python 3.x, pytest-8.x, pluggy-1.x',
             'rootdir: /work/sample',
             'collected 3 items', '']
    if passed_only:
        lines += ['tests/test_app.py ...                                                    [100%]',
                  '']
        if extra_summary:
            lines += ['=============================== warnings summary ===============================',
                      'tests/test_app.py::test_value',
                      '  /work/sample/tests/test_app.py:4: DeprecationWarning: synthetic',
                      '    warnings.warn("synthetic", DeprecationWarning)', '',
                      '-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html']
        lines += ['========================= 3 passed' + extra_summary + ' in 0.02s =========================']
        return ('\n'.join(lines) + '\n').encode('utf-8')
    lines += ['tests/test_app.py F.E                                                    [100%]', '',
              '==================================== ERRORS ====================================',
              '_____________________ ERROR at setup of test_with_fixture ______________________', '',
              '    @pytest.fixture', '    def resource():',
              '>       raise RuntimeError("synthetic setup failure")',
              'E       RuntimeError: synthetic setup failure', '',
              'tests/test_app.py:6: RuntimeError',
              '=================================== FAILURES ===================================',
              '__________________________________ test_value __________________________________', '',
              '    def test_value():', '>       assert compute() == 3',
              'E       assert 2 == 3', 'E        +  where 2 = compute()', '']
    lines += list(locals_)
    lines += ['', 'tests/test_app.py:11: AssertionError']
    for title, body in (('Captured stdout call', stdout), ('Captured stderr call', stderr),
                        ('Captured log call', log)):
        if body:
            lines.append('-' * 29 + ' ' + title + ' ' + '-' * 29)
            lines += list(body)
    lines += ['=========================== short test summary info ============================',
              'FAILED tests/test_app.py::test_value - assert 2 == 3',
              'ERROR tests/test_app.py::test_with_fixture - RuntimeError: synthetic setup failure',
              '===================== 1 failed, 1 passed, 1 error in 0.05s =====================']
    return ('\n'.join(lines) + '\n').encode('utf-8')


JUNIT_SAMPLE = (b'<?xml version="1.0" encoding="utf-8"?>\n'
                b'<testsuites><testsuite name="pytest" errors="1" failures="1" tests="3">\n'
                b'<testcase classname="tests.test_app" name="test_value">'
                b'<failure message="assert 2 == 3">synthetic</failure></testcase>\n'
                b'</testsuite></testsuites>\n')

PYTEST_IDS = ['tests/test_app.py::test_value', 'tests/test_app.py::test_with_fixture']
REPEATED = ['progress: synthetic step %04d ok' % index for index in range(1600)]


class CaptureFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.path = self.root / 'capture.log'

    def outcome(self, raw, exit_code):
        """Return (label, result) and keep immutable-source/confidentiality checks."""
        self.path.write_bytes(raw)
        os.chmod(self.path, 0o440)
        before = os.stat(self.path)
        try:
            result = select_test_excerpts(self.path, exit_code)
        except LLMToolError as error:
            message = str(error)
            self.assertIn(message, (SOURCE_REJECTED, EMITTED_REJECTED))
            label, result, wire = 'REJECTED', None, message
        else:
            label, wire = result['status'], json.dumps(result, ensure_ascii=True)
            self.assertEqual(result['test_exit_code'], exit_code)
            for excerpt in result['excerpts']:
                self.assertEqual(raw[excerpt['start_byte']:excerpt['end_byte']].decode('utf-8'),
                                 excerpt['text'])
        after = os.stat(self.path)
        self.assertEqual((before.st_mtime_ns, before.st_size, before.st_mode, before.st_ino),
                         (after.st_mtime_ns, after.st_size, after.st_mode, after.st_ino))
        self.assertEqual(self.path.read_bytes(), raw)
        # Real tracebacks name the throwaway script; the capture path itself
        # and the synthetic private marker must never be echoed.
        for private in (str(self.path), PRIVATE):
            self.assertNotIn(private, wire)
        os.chmod(self.path, 0o640)
        return label, result

    def throwaway_run(self, mode, names, *, verbosity=1, unbuffered=True):
        script = self.root / 'test_throwaway.py'
        script.write_text(THROWAWAY_TESTS, encoding='utf-8')
        command = [sys.executable, '-B', '-I'] + (['-u'] if unbuffered else [])
        done = subprocess.run(command + [script.name, mode, str(verbosity)] + list(names),
                              cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=CHILD_TIMEOUT_SECONDS,
                              env={'PATH': os.defpath, 'PYTHONIOENCODING': 'utf-8'})
        self.assertNotIn(str(self.path).encode(), done.stdout)
        return done.stdout, done.returncode


class BenignCaptureMeasurementTests(CaptureFixture):
    """Item 2: fixed curated denominators; not a production precision claim."""

    UNITTEST = [
        # label, mode, test names, verbosity, unbuffered, expected exit, expected outcome
        ('plain', 'plain', ('test_failure', 'test_error', 'test_pass'), 1, True, 1, 'COMPLETE'),
        ('all-pass-skip-xfail', 'plain', ('test_pass', 'test_skip', 'test_xfail'), 1, True, 0, 'COMPLETE'),
        ('root-log', 'root-log', ('test_failure', 'test_error'), 1, True, 1, 'COMPLETE'),
        ('module-log', 'module-log', ('test_failure', 'test_error'), 1, True, 1, 'COMPLETE'),
        ('basic-auth-disabled', 'basic-auth', ('test_failure',), 1, True, 1, 'REJECTED'),
        ('token-none', 'token-none', ('test_failure',), 1, True, 1, 'COMPLETE'),
        ('repeated-48k', 'repeated-48k', ('test_failure', 'test_error'), 1, True, 1, 'COMPLETE'),
        ('unicode', 'unicode', ('test_failure',), 1, True, 1, 'COMPLETE'),
        ('verbose', 'plain', ('test_failure', 'test_error', 'test_pass'), 2, True, 1, 'COMPLETE'),
        ('verbose-root-log', 'root-log', ('test_failure', 'test_error'), 2, True, 1, 'INCOMPLETE'),
        ('subtests', 'plain', ('test_subtests',), 1, True, 1, 'COMPLETE'),
        ('large-diff-48k', 'plain', ('test_large_diff',), 1, True, 1, 'COMPLETE'),
        ('buffered-stdout', 'repeated-48k', ('test_failure',), 1, False, 1, 'INCOMPLETE'),
    ]
    PYTEST = [
        ('failures', dict(), 1, 'COMPLETE'),
        ('all-pass', dict(passed_only=True), 0, 'COMPLETE'),
        ('warnings', dict(passed_only=True, extra_summary=', 1 warning'), 0, 'COMPLETE'),
        ('basic-auth-disabled', dict(stdout=['Basic auth disabled']), 1, 'REJECTED'),
        ('token-none-stdout', dict(stdout=['token = None']), 1, 'COMPLETE'),
        ('token-none-showlocals', dict(locals_=['token      = None', 'retries    = 3']), 1, 'COMPLETE'),
        ('captured-log', dict(log=['ERROR    root:test_app.py:10 synthetic observation']), 1, 'COMPLETE'),
        ('captured-stderr-root', dict(stderr=['ERROR:root:synthetic observation']), 1, 'COMPLETE'),
        ('repeated-48k', dict(stdout=REPEATED), 1, 'COMPLETE'),
        ('unicode', dict(stdout=['期望值不同 🙂 İ K ı']), 1, 'COMPLETE'),
        # A bare OK printed by a test also looks like a unittest summary.
        ('stdout-bare-ok', dict(stdout=['OK']), 1, 'INCOMPLETE'),
    ]

    def test_benign_capture_false_rejection_rates(self):
        measured, rows = {}, []
        for label, mode, names, verbosity, unbuffered, code, expected in self.UNITTEST:
            raw, exit_code = self.throwaway_run(mode, names, verbosity=verbosity, unbuffered=unbuffered)
            if label.endswith('48k'):
                self.assertGreater(len(raw), 48 * 1024)
            rows.append(('unittest-real', label, raw, exit_code, code, expected))
        for label, options, code, expected in self.PYTEST:
            raw = pytest_sample(**options)
            if label.endswith('48k'):
                self.assertGreater(len(raw), 48 * 1024)
            rows.append(('pytest-sanitized', label, raw, code, code, expected))
        rows.append(('junit-unsupported', 'junit-xml', JUNIT_SAMPLE, 1, 1, 'INCOMPLETE'))
        for family, label, raw, exit_code, code, expected in rows:
            with self.subTest(family=family, capture=label):
                self.assertEqual(exit_code, code)
                observed, result = self.outcome(raw, exit_code)
                measured.setdefault(family, Counter())[observed] += 1
                self.assertEqual(observed, expected, None if result is None
                                 else result['coverage']['unknown_reasons'])
                if result is not None and family == 'pytest-sanitized' and observed == 'COMPLETE' and code:
                    self.assertEqual(result['failure_ids'], PYTEST_IDS)
                if family == 'junit-unsupported':
                    self.assertEqual(result['detected_format'], 'unknown')
                    self.assertIn('unknown_format', result['coverage']['unknown_reasons'])
                    self.assertEqual(result['failure_ids'], [])
        self.assertEqual(measured, {
            'unittest-real': Counter(COMPLETE=10, INCOMPLETE=2, REJECTED=1),
            'pytest-sanitized': Counter(COMPLETE=9, INCOMPLETE=1, REJECTED=1),
            'junit-unsupported': Counter(INCOMPLETE=1)})
        print('Issue63 benign capture outcomes ' + json.dumps(
            {family: dict(sorted(counts.items())) for family, counts in measured.items()},
            sort_keys=True))


class FalseRejectionFixBothSidesTests(CaptureFixture):
    """A null/boolean assignment is benign; any other value stays rejected."""

    def unit_capture(self, line, *, crlf=False):
        body = ('Traceback (most recent call last):\n  File "tests/test_sample.py", line 8, in test_k\n'
                '    self.fail("evidence")\n' + line + '\nAssertionError: evidence')
        raw = ('=' * 70 + '\nFAIL: test_k (sample.Case.test_k)\n' + '-' * 70 + '\n' + body
               + '\n\n' + '-' * 70 + '\nRan 1 test in 0.010s\n\nFAILED (failures=1)\n')
        return (raw.replace('\n', '\r\n') if crlf else raw).encode('utf-8')

    def test_null_and_boolean_assignments_are_emitted(self):
        for line in ('token = None', 'password: null', 'api_key = NONE', '"secret": None',
                     'token      = None', 'client_secret: true', 'access_token=False  ',
                     '\token = None', 'x\ttoken: None'):
            for crlf in (False, True):
                with self.subTest(line=line, crlf=crlf):
                    observed, result = self.outcome(self.unit_capture(line, crlf=crlf), 1)
                    self.assertEqual(observed, 'COMPLETE')
                    self.assertIn(line.rstrip(), result['excerpts'][0]['text'])

    def test_other_values_after_keywords_remain_rejected(self):
        for line in ('token = ' + PRIVATE, 'token = None or ' + PRIVATE, 'token = None ' + PRIVATE,
                     'token = None, ' + PRIVATE, 'token = Nonesense' + PRIVATE,
                     'token = None\\n' + PRIVATE, 'token = None; ' + PRIVATE,
                     "{'token': None, 'secret': '" + PRIVATE + "'}",
                     'password = null # ' + PRIVATE, 'token = true' + PRIVATE,
                     'token == ' + PRIVATE, 'token = <REDACTED>',
                     'token = None Bearer ' + PRIVATE):
            with self.subTest(line=line.replace(PRIVATE, '<private>')):
                observed, _ = self.outcome(self.unit_capture(line), 1)
                self.assertEqual(observed, 'REJECTED')

    def test_json_tab_join_still_rejects_non_null_values(self):
        # The final gate screens the JSON-escaped payload: TAB + "oken = v".
        for tail, expected in (('\token = ' + PRIVATE, 'REJECTED'), ('\token = None', 'COMPLETE'),
                               ('\token = None\t' + PRIVATE, 'REJECTED')):
            with self.subTest(expected=expected):
                observed, _ = self.outcome(self.unit_capture(tail), 1)
                self.assertEqual(observed, expected)


class PytestLogLineTests(CaptureFixture):
    def test_log_lines_before_summary_banner_do_not_mark_incomplete(self):
        for line in ('ERROR    root:test_app.py:10 synthetic observation',
                     'ERROR    app.db:db.py:77 synthetic retry', 'ERROR:root:synthetic observation',
                     'ERROR:app.db:synthetic retry'):
            with self.subTest(line=line):
                observed, result = self.outcome(pytest_sample(log=[line]), 1)
                self.assertEqual(observed, 'COMPLETE')
                self.assertEqual(result['failure_ids'], PYTEST_IDS)
                self.assertEqual(result['coverage']['observed_counts'], {'FAIL': 1, 'ERROR': 1})

    def test_failure_like_lines_stay_incomplete_or_rejected(self):
        after_banner = pytest_sample().replace(
            b'FAILED tests/test_app.py::test_value', b'ERROR:root:synthetic\nFAILED tests/test_app.py::test_value')
        cases = [(pytest_sample(log=['ERROR tests/test_app.py::test_hidden']), 'INCOMPLETE'),
                 (pytest_sample(log=['ERROR', 'ERROR: synthetic']), 'INCOMPLETE'),
                 (pytest_sample(log=['FAILED tests/test_app.py::test_hidden']), 'INCOMPLETE'),
                 (after_banner, 'INCOMPLETE'),
                 (pytest_sample(log=['ERROR:root:token = ' + PRIVATE]), 'REJECTED'),
                 (pytest_sample(log=['ERROR    root:a.py:1 Bearer ' + PRIVATE]), 'REJECTED')]
        for raw, expected in cases:
            with self.subTest(expected=expected):
                observed, result = self.outcome(raw, 1)
                self.assertEqual(observed, expected)
                if result is not None:
                    self.assertNotIn('tests/test_app.py::test_hidden', result['failure_ids'])


    def test_unittest_named_logger_line_only_exempt_when_unanchored(self):
        body = ('Traceback (most recent call last):\n  File "t.py", line 1, in test_k\n'
                'ValueError: evidence\n')
        def capture(header):
            return ('ERROR:app.db:synthetic retry\n' + '=' * 70 + '\n' + header + '\n' + '-' * 70
                    + '\n' + body + '\n' + '-' * 70 + '\nRan 1 test in 0.010s\n\nFAILED (errors=1)\n'
                    ).encode('utf-8')
        observed, result = self.outcome(capture('ERROR: test_k (sample.Case.test_k)'), 1)
        self.assertEqual((observed, result['failure_ids']), ('COMPLETE', ['test_k (sample.Case.test_k)']))
        # A separator-anchored logger-shaped header is ambiguous: keep it malformed.
        observed, result = self.outcome(capture('ERROR:app.db:test_k'), 1)
        self.assertEqual(observed, 'INCOMPLETE')
        self.assertIn('malformed_failure_header', result['coverage']['unknown_reasons'])
        observed, _ = self.outcome(capture('ERROR: test_k (sample.Case.test_k)').replace(
            b'synthetic retry', b'password=' + PRIVATE.encode()), 1)
        self.assertEqual(observed, 'REJECTED')


class RealProcessExitTests(CaptureFixture):
    """Item 3: real subprocess pipe closure, signals and exit codes; no mocks."""

    def test_real_unittest_capture_with_success_exit(self):
        raw, code = self.throwaway_run('plain', ('test_pass', 'test_skip'))
        self.assertEqual(code, 0)
        observed, result = self.outcome(raw, code)
        self.assertEqual((observed, result['test_status']), ('COMPLETE', 'NO_FAILURES_REPORTED'))
        observed, result = self.outcome(raw, 1)
        self.assertEqual((observed, result['test_status']), ('INCOMPLETE', 'FAILED'))

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX pipes')
    def test_reader_closed_pipe_capture_is_never_complete(self):
        script = self.root / 'test_throwaway.py'
        script.write_text(THROWAWAY_TESTS, encoding='utf-8')
        child = subprocess.Popen([sys.executable, '-B', '-I', '-u', script.name, 'flood', '1',
                                  'test_failure', 'test_error'], cwd=self.root,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, env={'PATH': os.defpath})
        try:
            chunks, size = [], 0
            while size < 8192:
                chunk = os.read(child.stdout.fileno(), 8192 - size)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            child.stdout.close()  # The flood far exceeds any pipe buffer.
            code = child.wait(timeout=CHILD_TIMEOUT_SECONDS)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
        raw = b''.join(chunks)
        self.assertEqual(len(raw), 8192)
        self.assertNotEqual(code, 0)
        self.assertTrue(-127 <= code <= 255)
        observed, result = self.outcome(raw, code)
        self.assertEqual((observed, result['test_status']), ('INCOMPLETE', 'FAILED'))
        self.assertEqual(result['failure_ids'], [])
        self.assertIn('nonzero_exit_without_failure_ids', result['coverage']['unknown_reasons'])

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX signals')
    def test_signal_terminated_runner_is_incomplete_with_negative_exit(self):
        raw, code = self.throwaway_run('sigterm', ('test_pass', 'test_failure', 'test_error'))
        self.assertEqual(code, -signal.SIGTERM)
        observed, result = self.outcome(raw, code)
        self.assertEqual((observed, result['test_status']), ('INCOMPLETE', 'FAILED'))
        self.assertEqual(result['test_exit_code'], -15)

    def test_forced_zero_exit_with_real_failures_is_inconsistent(self):
        raw, code = self.throwaway_run('force-zero', ('test_failure', 'test_error'))
        self.assertEqual(code, 0)
        observed, result = self.outcome(raw, code)
        self.assertEqual((observed, result['test_status']), ('INCOMPLETE', 'FAILED'))
        self.assertIn('exit_summary_mismatch', result['coverage']['unknown_reasons'])
        self.assertEqual(len(result['failure_ids']), 2)

    @unittest.skipUnless(sys.version_info >= (3, 12), 'unittest exit 5 for no tests is 3.12+')
    def test_no_tests_ran_exit_five_is_incomplete(self):
        script = self.root / 'test_empty.py'
        script.write_text('import unittest\nunittest.main()\n', encoding='utf-8')
        done = subprocess.run([sys.executable, '-B', '-I', '-u', script.name],
                              cwd=self.root, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=CHILD_TIMEOUT_SECONDS,
                              env={'PATH': os.defpath})
        self.assertEqual(done.returncode, 5)
        observed, result = self.outcome(done.stdout, done.returncode)
        self.assertEqual((observed, result['test_status']), ('INCOMPLETE', 'FAILED'))


class NearLimitUnicodeJwtTests(CaptureFixture):
    """Item 3: adversarial near-1 MiB JWT-shaped inputs with a wall-clock bound."""

    def child_select(self, raw):
        self.path.write_bytes(raw)
        os.chmod(self.path, 0o440)
        before = os.stat(self.path)
        program = ('import json, sys, time\n'
                   'from llmtool.errors import LLMToolError\n'
                   'from llmtool.test_output_excerpts import select_test_excerpts\n'
                   'start = time.perf_counter()\n'
                   'try:\n'
                   '    outcome = select_test_excerpts(sys.argv[1], 1)["status"]\n'
                   'except LLMToolError as error:\n'
                   '    outcome = str(error)\n'
                   'print(json.dumps([outcome, time.perf_counter() - start]))\n')
        env = {'PATH': os.defpath, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
               'PYTHONDONTWRITEBYTECODE': '1'}
        done = subprocess.run([sys.executable, '-B', '-c', program, str(self.path)],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env,
                              cwd=self.root, timeout=CHILD_TIMEOUT_SECONDS, check=True)
        self.assertEqual(done.stderr, '')
        after = os.stat(self.path)
        self.assertEqual((before.st_mtime_ns, before.st_size, before.st_mode, before.st_ino),
                         (after.st_mtime_ns, after.st_size, after.st_mode, after.st_ino))
        self.assertEqual(self.path.read_bytes(), raw)
        outcome, elapsed = json.loads(done.stdout)
        # Same confidentiality checks as outcome(): no workspace/capture path
        # and no synthetic private marker in the child's output.
        for private in (str(self.root), str(self.path), PRIVATE):
            self.assertNotIn(private, done.stdout)
        os.chmod(self.path, 0o640)
        return outcome, elapsed

    def fill(self, prefix, unit, suffix):
        budget = MAX_FILE_BYTES - 1 - len(prefix) - len(suffix)
        raw = prefix + unit * (budget // len(unit)) + suffix
        self.assertGreater(len(raw), MAX_FILE_BYTES - 1 - len(unit))
        self.assertLessEqual(len(raw), MAX_FILE_BYTES)
        return raw

    def test_near_limit_unicode_jwt_shapes_have_bounded_oracles(self):
        jwt_tail = b'eyJ' + b'a' * 8 + b'.' + b'b' * 8 + b'.' + b'c' * 8
        kelvin, dotless = 'K'.encode(), 'ı'.encode()
        cases = [
            # Many adjacent short segments, then one real JWT shape at the end.
            ('short-segments-then-jwt', b'', b'a.', b'\n' + jwt_tail + b'\n', SOURCE_REJECTED),
            # IGNORECASE makes KELVIN SIGN a JWT segment letter.
            ('kelvin-segments', b'eyJ', kelvin, b'.' + kelvin * 8 + b'.' + kelvin * 8 + b'\n', SOURCE_REJECTED),
            # A combining mark is not a word character, so it cannot hide the tail.
            ('combining-tail', b'', 'x 測試 '.encode(), b'\n' + jwt_tail + '́'.encode() + b'\n',
             SOURCE_REJECTED),
            # A non-segment character after each dot breaks adjacency.
            ('broken-adjacency', b'', b'eyJ' + b'a' * 8 + '.測'.encode(), b'\n', 'INCOMPLETE'),
            # Two-segment Unicode chains never acquire a third segment.
            ('two-segment-chains', b'', b'eyJ' + dotless * 8 + b'.' + dotless * 8 + b' ', b'\n', 'INCOMPLETE'),
            # One huge dash segment with a too-short second segment.
            ('huge-segment-short-second', b'eyJ', b'-', b'.' + b'-' * 7 + b'\n', 'INCOMPLETE'),
        ]
        timings = {}
        for label, prefix, unit, suffix, expected in cases:
            with self.subTest(case=label):
                outcome, elapsed = self.child_select(self.fill(prefix, unit, suffix))
                self.assertEqual(outcome, expected)
                self.assertLess(elapsed, PERF_BOUND_SECONDS)
                timings[label] = round(elapsed, 3)
        print('Issue63 near-limit JWT seconds ' + json.dumps(timings, sort_keys=True))

    def test_near_limit_null_assignment_exemption_is_linear_and_exact(self):
        cases = [
            # Pad lines to stay under the 32768-line source bound.
            ('many-exempt-lines', b'', b'x' * 24 + b' token = None\n', b'', 'INCOMPLETE'),
            ('long-gap-then-null', b'token =', b' ', b'None\n', 'INCOMPLETE'),
            ('long-gap-then-value', b'token =', b' ', b'None ' + PRIVATE.encode() + b'\n', SOURCE_REJECTED),
            ('exempt-lines-then-value', b'', b'x' * 24 + b' password: null\n',b'password: ' + PRIVATE.encode() + b'\n',
             SOURCE_REJECTED),
        ]
        for label, prefix, unit, suffix, expected in cases:
            with self.subTest(case=label):
                outcome, elapsed = self.child_select(self.fill(prefix, unit, suffix))
                self.assertEqual(outcome, expected)
                self.assertLess(elapsed, PERF_BOUND_SECONDS)


if __name__ == '__main__':
    unittest.main()
