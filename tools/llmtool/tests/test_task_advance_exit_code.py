"""#1519: `task advance` exit code for allowed (step, code) pairs."""
import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from llmtool import task_submission_cli
from llmtool.cli import parser_for_cli

NATIVE = '3f2b8c1e-4d5a-4b6c-8d7e-9f0a1b2c3d4e'


class AdvanceExitCodeTests(unittest.TestCase):
    def _run(self, result):
        args = parser_for_cli().parse_args(
            ['task', 'advance', '1' * 32, '--pm-session', NATIVE])
        with patch('llmtool.task_advance.advance', return_value=result) as advance, \
                patch('llmtool.pm_caller.check_caller', return_value='pm-caller'), \
                patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': '', 'CODEX_THREAD_ID': ''}), \
                redirect_stdout(io.StringIO()):
            status = task_submission_cli.run_task_advance(args, store=object())
        advance.assert_called_once()
        return status

    def test_merged_with_merge_reconciled_exits_zero(self):
        self.assertEqual(self._run({'step': 'MERGED', 'codes': ['MERGE_RECONCILED']}), 0)

    def test_ready_for_integration_with_waiting_integration_exits_zero(self):
        self.assertEqual(self._run({'step': 'READY_FOR_INTEGRATION',
                                    'codes': ['WAITING_INTEGRATION']}), 0)

    def test_success_step_with_failure_code_exits_one(self):
        self.assertEqual(self._run({'step': 'MERGED',
                                    'codes': ['MERGE_PROVENANCE_COMMENT_FAILED']}), 1)

    def test_mixed_allowed_and_failure_codes_exit_one(self):
        self.assertEqual(self._run({'step': 'MERGED',
                                    'codes': ['MERGE_RECONCILED', 'MERGE_RESULT_RECORD_FAILED']}), 1)

    def test_allowed_code_on_other_step_exits_one(self):
        self.assertEqual(self._run({'step': 'MERGE_REFUSED',
                                    'codes': ['MERGE_RECONCILED']}), 1)

    def test_allowed_code_with_the_other_pairs_step_exits_one(self):
        self.assertEqual(self._run({'step': 'MERGED',
                                    'codes': ['WAITING_INTEGRATION']}), 1)
        self.assertEqual(self._run({'step': 'CLOSED',
                                    'codes': ['MERGE_RECONCILED']}), 1)


if __name__ == '__main__':
    unittest.main()
