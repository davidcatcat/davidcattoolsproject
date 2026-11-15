"""Tests pinning runtime failure codes in review_dispatch._dispatch_codex."""

import tempfile
import unittest
from unittest.mock import patch

from llmtool import review_dispatch
from llmtool.errors import LLMToolError


TASK = 'a' * 32
HEAD = 'b' * 40
EXPECTED_REFUSAL_MESSAGE = 'Reviewer dispatch refused; details omitted'


class TestReviewDispatchRuntimeCodes(unittest.TestCase):
    """Pin review_dispatch._dispatch_codex runtime refusal codes and messages."""

    def test_case_1_which_none_refuses_runtime_unavailable_and_does_not_run_subprocess(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(review_dispatch.shutil, 'which', return_value=None), \
                 patch.object(review_dispatch.subprocess, 'run') as mock_run:
                with self.assertRaises(LLMToolError) as ctx:
                    review_dispatch._dispatch_codex(
                        reviewer_root=temp_dir,
                        prompt='p',
                        task={'task_id': TASK},
                        head=HEAD,
                    )
                self.assertEqual(ctx.exception.review_dispatch_code, 'REVIEWER_RUNTIME_UNAVAILABLE')
                self.assertEqual(str(ctx.exception), EXPECTED_REFUSAL_MESSAGE)
                mock_run.assert_not_called()

    def test_case_2_oserror_refuses_runtime_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(review_dispatch.shutil, 'which', return_value='/usr/bin/true'), \
                 patch.object(review_dispatch.subprocess, 'run', side_effect=OSError) as mock_run:
                with self.assertRaises(LLMToolError) as ctx:
                    review_dispatch._dispatch_codex(
                        reviewer_root=temp_dir,
                        prompt='p',
                        task={'task_id': TASK},
                        head=HEAD,
                    )
                self.assertEqual(ctx.exception.review_dispatch_code, 'REVIEWER_RUNTIME_UNAVAILABLE')
                self.assertEqual(str(ctx.exception), EXPECTED_REFUSAL_MESSAGE)
                mock_run.assert_called_once()

    def test_case_3_timeout_expired_refuses_reviewer_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(review_dispatch.shutil, 'which', return_value='/usr/bin/true'), \
                 patch.object(
                     review_dispatch.subprocess,
                     'run',
                     side_effect=review_dispatch.subprocess.TimeoutExpired('codex', 1),
                 ) as mock_run:
                with self.assertRaises(LLMToolError) as ctx:
                    review_dispatch._dispatch_codex(
                        reviewer_root=temp_dir,
                        prompt='p',
                        task={'task_id': TASK},
                        head=HEAD,
                    )
                self.assertEqual(ctx.exception.review_dispatch_code, 'REVIEWER_TIMEOUT')
                mock_run.assert_called_once()

    def test_case_4_refusal_message_fixed_text_for_cases_1_and_2(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(review_dispatch.shutil, 'which', return_value=None), \
                 patch.object(review_dispatch.subprocess, 'run') as mock_run:
                with self.assertRaises(LLMToolError) as ctx1:
                    review_dispatch._dispatch_codex(
                        reviewer_root=temp_dir,
                        prompt='p',
                        task={'task_id': TASK},
                        head=HEAD,
                    )
                self.assertEqual(str(ctx1.exception), EXPECTED_REFUSAL_MESSAGE)
                mock_run.assert_not_called()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(review_dispatch.shutil, 'which', return_value='/usr/bin/true'), \
                 patch.object(review_dispatch.subprocess, 'run', side_effect=OSError):
                with self.assertRaises(LLMToolError) as ctx2:
                    review_dispatch._dispatch_codex(
                        reviewer_root=temp_dir,
                        prompt='p',
                        task={'task_id': TASK},
                        head=HEAD,
                    )
                self.assertEqual(str(ctx2.exception), EXPECTED_REFUSAL_MESSAGE)


if __name__ == '__main__':
    unittest.main()
