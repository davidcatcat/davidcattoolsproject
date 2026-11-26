"""#2761: a failed hosted check's job log fills the CI repair packet details."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import review_repair, task_advance


HEAD = 'a' * 40
JOB_ID = 9002


def _run(details_url='https://example.test/actions/runs/9001/job/9002', output=None):
    run = {
        'id': 42,
        'name': 'check (Linux 3.14)',
        'head_sha': HEAD,
        'status': 'completed',
        'conclusion': 'failure',
        'details_url': details_url,
    }
    if output is not None:
        run['output'] = output
    return run


class CiJobLog2761Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        tests = self.root / 'tools' / 'llmtool' / 'tests'
        tests.mkdir(parents=True)
        (tests / 'test_alpha.py').write_text('import unittest\n', encoding='utf-8')
        (tests / 'test_beta.py').write_text('import unittest\n', encoding='utf-8')

    def test_real_shaped_log_gives_failing_tests_and_admissible_modules(self):
        log = '\n'.join([
            '2026-11-26T00:00:00.0000000Z ##[group]Run python -m unittest',
            'FAIL: test_alpha_one (test_alpha.AlphaTests)',
            'AssertionError: expected 1',
            'ERROR: test_beta_two (test_beta.BetaTests.test_beta_two)',
            'AssertionError: boom',
        ])
        with patch.object(task_advance, '_fetch_ci_job_log',
                          return_value=log) as fetch:
            details = task_advance._ci_failure_details(_run(), str(self.root))
        fetch.assert_called_once_with(JOB_ID)
        excerpt, failing_tests, failing_modules, workflow_run_id, job_id, unavailable = details
        self.assertEqual(failing_tests, [
            'test_alpha_one (test_alpha.AlphaTests)',
            'test_beta_two (test_beta.BetaTests.test_beta_two)',
        ])
        self.assertEqual(failing_modules, [
            'tools/llmtool/tests/test_alpha.py',
            'tools/llmtool/tests/test_beta.py',
        ])
        self.assertIn('AssertionError: expected 1', excerpt)
        self.assertEqual((workflow_run_id, job_id), (9001, JOB_ID))
        self.assertFalse(unavailable)

    def test_404_log_keeps_the_unavailable_packet(self):
        with patch.object(task_advance.task_completion, '_trusted_slug',
                          return_value='owner/repo'), \
                patch.object(task_advance, '_publish_run_gh',
                             return_value=subprocess.CompletedProcess(
                                 ['gh'], 1, b'', b'')):
            details = task_advance._ci_failure_details(_run(), str(self.root))
        excerpt, failing_tests, failing_modules, workflow_run_id, job_id, unavailable = details
        self.assertEqual((excerpt, failing_tests, failing_modules), ('', [], []))
        self.assertEqual((workflow_run_id, job_id), (9001, JOB_ID))
        self.assertTrue(unavailable)

    def test_present_check_run_output_is_used_without_reading_the_log(self):
        output = {'title': 'Linux CI failed',
                  'summary': 'FAIL: test_alpha_one (test_alpha.AlphaTests)'}
        with patch.object(task_advance, '_fetch_ci_job_log',
                          side_effect=AssertionError('log must not be read')) as fetch:
            details = task_advance._ci_failure_details(_run(output=output), str(self.root))
        fetch.assert_not_called()
        excerpt, failing_tests, failing_modules, _run_id, _job_id, unavailable = details
        self.assertEqual(failing_tests, ['test_alpha_one (test_alpha.AlphaTests)'])
        self.assertEqual(failing_modules, ['tools/llmtool/tests/test_alpha.py'])
        self.assertIn('Linux CI failed', excerpt)
        self.assertFalse(unavailable)

    def test_oversize_log_is_bounded_and_an_over_cap_log_is_unavailable(self):
        log = ('noise\n' * 90000
               + 'FAIL: test_alpha_one (test_alpha.AlphaTests)\n'
               + 'AssertionError: expected 1\n')
        self.assertGreater(len(log.encode('utf-8')), task_advance._CI_JOB_LOG_TAIL_BYTES)
        with patch.object(task_advance, '_fetch_ci_job_log', return_value=log):
            details = task_advance._ci_failure_details(_run(), str(self.root))
        excerpt, failing_tests, failing_modules, _run_id, _job_id, unavailable = details
        self.assertEqual(failing_tests, ['test_alpha_one (test_alpha.AlphaTests)'])
        self.assertEqual(failing_modules, ['tools/llmtool/tests/test_alpha.py'])
        self.assertLessEqual(len(excerpt), review_repair.MAX_EXCERPT_CHARS)
        self.assertLessEqual(len(excerpt.encode('utf-8')),
                             review_repair.CI_EXCERPT_MAX_BYTES)
        self.assertFalse(unavailable)

        over_cap = 'x' * (task_advance._CI_JOB_LOG_HARD_CAP_BYTES + 1)
        with patch.object(task_advance, '_fetch_ci_job_log', return_value=over_cap):
            details = task_advance._ci_failure_details(_run(), str(self.root))
        self.assertEqual(details[:3], ('', [], []))
        self.assertTrue(details[-1])

    def test_fetch_uses_the_existing_gh_credential_path_and_rejects_non_utf8(self):
        log = b'FAIL: test_alpha_one (test_alpha.AlphaTests)\n'
        with patch.object(task_advance.task_completion, '_trusted_slug',
                          return_value='owner/repo'), \
                patch.object(task_advance, '_publish_run_gh',
                             return_value=subprocess.CompletedProcess(
                                 ['gh'], 0, log, b'')) as runner:
            self.assertEqual(task_advance._fetch_ci_job_log(JOB_ID),
                             log.decode('utf-8'))
        runner.assert_called_once()
        self.assertIn(f'repos/owner/repo/actions/jobs/{JOB_ID}/logs',
                      runner.call_args.args[1])
        with patch.object(task_advance.task_completion, '_trusted_slug',
                          return_value='owner/repo'), \
                patch.object(task_advance, '_publish_run_gh',
                             return_value=subprocess.CompletedProcess(
                                 ['gh'], 0, b'FAIL: \xff\n', b'')):
            self.assertIsNone(task_advance._fetch_ci_job_log(JOB_ID))
        with patch.object(task_advance.task_completion, '_trusted_slug',
                          return_value='owner/repo'), \
                patch.object(task_advance, '_publish_run_gh',
                             return_value=subprocess.CompletedProcess(
                                 ['gh'], 1, b'', b'')):
            self.assertIsNone(task_advance._fetch_ci_job_log(JOB_ID))

    def test_fetch_command_allows_escape_sequences(self):
        log = b'FAIL: test_alpha_one (test_alpha.AlphaTests)\n'
        with patch.object(task_advance.task_completion, '_trusted_slug',
                          return_value='owner/repo'), \
                patch.object(task_advance, '_publish_run_gh',
                             return_value=subprocess.CompletedProcess(
                                 ['gh'], 0, log, b'')) as runner:
            self.assertEqual(task_advance._fetch_ci_job_log(JOB_ID),
                             log.decode('utf-8'))
        self.assertEqual(runner.call_args.args[1], [
            'api', '--allow-escape-sequences', '--method', 'GET',
            f'repos/owner/repo/actions/jobs/{JOB_ID}/logs'])

    def test_ansi_coloured_log_yields_names_and_modules_without_escape_bytes(self):
        esc = '\x1b'
        log = '\n'.join([
            f'{esc}[36;1mFAIL: test_alpha_one (test_alpha.AlphaTests){esc}[0m',
            f'{esc}[31mAssertionError: expected 1{esc}[0m',
            f'{esc}[36;1mERROR: test_beta_two (test_beta.BetaTests.test_beta_two){esc}[0m',
            f'{esc}[31mAssertionError: boom{esc}[0m',
        ])
        with patch.object(task_advance, '_fetch_ci_job_log', return_value=log):
            details = task_advance._ci_failure_details(_run(), str(self.root))
        excerpt, failing_tests, failing_modules, _run_id, _job_id, unavailable = details
        self.assertEqual(failing_tests, [
            'test_alpha_one (test_alpha.AlphaTests)',
            'test_beta_two (test_beta.BetaTests.test_beta_two)',
        ])
        self.assertEqual(failing_modules, [
            'tools/llmtool/tests/test_alpha.py',
            'tools/llmtool/tests/test_beta.py',
        ])
        self.assertIn('AssertionError: expected 1', excerpt)
        self.assertNotIn('\x1b', excerpt)
        self.assertFalse(unavailable)


if __name__ == '__main__':
    unittest.main()
