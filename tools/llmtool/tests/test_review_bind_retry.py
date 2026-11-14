"""#1909 item 3: retry _bind_d6 when the helper times out or fails to start."""

from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from llmtool import review_dispatch, review_result


TASK_ID = 'reviewer-task-1'
SESSION_ID = 'session-1'
REPO_ROOT = '/repo/root'


class ReviewBindRetryTest(unittest.TestCase):

    @patch('llmtool.review_dispatch.subprocess.run')
    def test_first_run_exit_0_returns(self, mock_run):
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=['lifecycle'], returncode=0, stdout=b'', stderr=b''),
        ]
        result = review_dispatch._bind_d6(TASK_ID, SESSION_ID, REPO_ROOT)
        self.assertIsNone(result)
        self.assertEqual(mock_run.call_count, 1)

    @patch('llmtool.review_dispatch.subprocess.run')
    def test_first_run_exit_1_raises_bind_failed(self, mock_run):
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=['lifecycle'], returncode=1, stdout=b'', stderr=b''),
        ]
        with self.assertRaises(RuntimeError) as cm:
            review_dispatch._bind_d6(TASK_ID, SESSION_ID, REPO_ROOT)
        self.assertEqual(str(cm.exception), 'REVIEWER_SESSION_BIND_FAILED')
        self.assertEqual(mock_run.call_count, 1)

    @patch('llmtool.review_result._reviewer_session_bound')
    @patch('llmtool.review_dispatch.subprocess.run')
    def test_first_run_exit_1_with_already_set_stderr_raises_bind_failed(
            self, mock_run, mock_bound):
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=['lifecycle'], returncode=1, stdout=b'',
                stderr=b'D6_BIND_NATIVE_SESSION_ALREADY_SET\n'),
        ]
        with self.assertRaises(RuntimeError) as cm:
            review_dispatch._bind_d6(TASK_ID, SESSION_ID, REPO_ROOT)
        self.assertEqual(str(cm.exception), 'REVIEWER_SESSION_BIND_FAILED')
        self.assertEqual(mock_run.call_count, 1)
        mock_bound.assert_not_called()

    @patch('llmtool.review_dispatch.subprocess.run')
    def test_timeout_then_exit_0_returns(self, mock_run):
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd=['lifecycle'], timeout=120),
            subprocess.CompletedProcess(
                args=['lifecycle'], returncode=0, stdout=b'', stderr=b''),
        ]
        result = review_dispatch._bind_d6(TASK_ID, SESSION_ID, REPO_ROOT)
        self.assertIsNone(result)
        self.assertEqual(mock_run.call_count, 2)

    @patch('llmtool.review_dispatch.subprocess.run')
    def test_oserror_then_exit_0_returns(self, mock_run):
        mock_run.side_effect = [
            OSError('process start failed'),
            subprocess.CompletedProcess(
                args=['lifecycle'], returncode=0, stdout=b'', stderr=b''),
        ]
        result = review_dispatch._bind_d6(TASK_ID, SESSION_ID, REPO_ROOT)
        self.assertIsNone(result)
        self.assertEqual(mock_run.call_count, 2)

    @patch('llmtool.review_dispatch.subprocess.run')
    def test_timeout_twice_raises_bind_timeout(self, mock_run):
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd=['lifecycle'], timeout=120),
            subprocess.TimeoutExpired(cmd=['lifecycle'], timeout=120),
        ]
        with self.assertRaises(RuntimeError) as cm:
            review_dispatch._bind_d6(TASK_ID, SESSION_ID, REPO_ROOT)
        self.assertEqual(str(cm.exception), 'REVIEWER_SESSION_BIND_TIMEOUT')
        self.assertEqual(mock_run.call_count, 2)

    @patch('llmtool.review_dispatch.subprocess.run')
    def test_oserror_twice_raises_bind_timeout(self, mock_run):
        mock_run.side_effect = [
            OSError('process start failed'),
            OSError('retry process start failed'),
        ]
        with self.assertRaises(RuntimeError) as cm:
            review_dispatch._bind_d6(TASK_ID, SESSION_ID, REPO_ROOT)
        self.assertEqual(str(cm.exception), 'REVIEWER_SESSION_BIND_TIMEOUT')
        self.assertEqual(mock_run.call_count, 2)

    @patch('llmtool.review_result._reviewer_session_bound', return_value=True)
    @patch('llmtool.review_dispatch.subprocess.run')
    def test_timeout_then_already_set_session_check_true_returns(
            self, mock_run, mock_bound):
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd=['lifecycle'], timeout=120),
            subprocess.CompletedProcess(
                args=['lifecycle'], returncode=1, stdout=b'',
                stderr=b'D6_BIND_NATIVE_SESSION_ALREADY_SET\n'),
        ]
        result = review_dispatch._bind_d6(TASK_ID, SESSION_ID, REPO_ROOT)
        self.assertIsNone(result)
        self.assertEqual(mock_run.call_count, 2)
        mock_bound.assert_called_once_with(
            {'reviewer_task': TASK_ID, 'native_session_id': SESSION_ID}, None)

    @patch('llmtool.review_result._reviewer_session_bound', return_value=False)
    @patch('llmtool.review_dispatch.subprocess.run')
    def test_timeout_then_already_set_session_check_false_raises_bind_failed(
            self, mock_run, mock_bound):
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd=['lifecycle'], timeout=120),
            subprocess.CompletedProcess(
                args=['lifecycle'], returncode=1, stdout=b'',
                stderr=b'D6_BIND_NATIVE_SESSION_ALREADY_SET\n'),
        ]
        with self.assertRaises(RuntimeError) as cm:
            review_dispatch._bind_d6(TASK_ID, SESSION_ID, REPO_ROOT)
        self.assertEqual(str(cm.exception), 'REVIEWER_SESSION_BIND_FAILED')
        self.assertEqual(mock_run.call_count, 2)
        mock_bound.assert_called_once_with(
            {'reviewer_task': TASK_ID, 'native_session_id': SESSION_ID}, None)

    @patch('llmtool.review_result._reviewer_session_bound')
    @patch('llmtool.review_dispatch.subprocess.run')
    def test_timeout_then_different_stderr_raises_bind_failed(
            self, mock_run, mock_bound):
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd=['lifecycle'], timeout=120),
            subprocess.CompletedProcess(
                args=['lifecycle'], returncode=1, stdout=b'',
                stderr=b'SOME_OTHER_ERROR\n'),
        ]
        with self.assertRaises(RuntimeError) as cm:
            review_dispatch._bind_d6(TASK_ID, SESSION_ID, REPO_ROOT)
        self.assertEqual(str(cm.exception), 'REVIEWER_SESSION_BIND_FAILED')
        self.assertEqual(mock_run.call_count, 2)
        mock_bound.assert_not_called()

    @patch('llmtool.review_dispatch.subprocess.run')
    def test_both_runs_use_timeout_120_and_identical_parameters(self, mock_run):
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd=['lifecycle'], timeout=120),
            subprocess.CompletedProcess(
                args=['lifecycle'], returncode=0, stdout=b'', stderr=b''),
        ]
        review_dispatch._bind_d6(TASK_ID, SESSION_ID, REPO_ROOT)
        self.assertEqual(mock_run.call_count, 2)
        call1, call2 = mock_run.call_args_list
        self.assertEqual(call1.kwargs.get('timeout'), 120)
        self.assertEqual(call2.kwargs.get('timeout'), 120)
        self.assertEqual(call1.args, call2.args)
        self.assertEqual(call1.kwargs, call2.kwargs)
        self.assertEqual(call1.kwargs.get('check'), False)
        self.assertEqual(call1.kwargs.get('cwd'), str(REPO_ROOT))
        self.assertEqual(call1.kwargs.get('stdin'), subprocess.DEVNULL)
        self.assertEqual(call1.kwargs.get('stdout'), subprocess.PIPE)
        self.assertEqual(call1.kwargs.get('stderr'), subprocess.PIPE)


if __name__ == '__main__':
    unittest.main()
