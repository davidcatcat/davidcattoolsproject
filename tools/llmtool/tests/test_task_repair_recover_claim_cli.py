"""#1928 PR5: task repair --recover-claim runs only recover_unknown_claim."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

from llmtool import pm_caller, review_repair, review_repair_recovery, task_submission_cli
from llmtool.cli import parser_for_cli
from llmtool.errors import LLMToolError

TASK, REPAIR = 'a' * 32, 'b' * 32


class RecoverClaimCliTests(unittest.TestCase):
    def run_cli(self, *extra):
        args = parser_for_cli().parse_args(['task', 'repair', TASK, '--executor-task', 'e-1',
                                          '--pr', '17', *extra])
        output = io.StringIO()
        with mock.patch.object(pm_caller, 'check_caller', return_value='pm-1') as caller, \
                mock.patch.object(review_repair_recovery, 'recover_unknown_claim',
                                  return_value={'recovered': True, 'claim': {}}) as recover, \
                mock.patch.object(review_repair, 'repair_review',
                                  return_value={'state': 'REPAIRED'}) as repair, \
                redirect_stdout(output):
            code = task_submission_cli.run_task_repair(args, store=mock.Mock())
        return code, output.getvalue(), caller, recover, repair

    def test_recover_claim_calls_only_the_recovery(self):
        code, output, caller, recover, repair = self.run_cli('--recover-claim', REPAIR)
        caller.assert_called_once()
        recover.assert_called_once_with(store=mock.ANY, task_id=TASK, executor_task='e-1',
                                        pr_number=17, repair_task_id=REPAIR)
        repair.assert_not_called()
        self.assertEqual(json.loads(output), {'recovered': True, 'claim': {}})
        self.assertIsNone(code)

    def test_recover_claim_refuses_review_options(self):
        for extra in (('--auto-review',), ('--family', 'pr-17')):
            with self.subTest(extra=extra), self.assertRaises(LLMToolError):
                self.run_cli('--recover-claim', REPAIR, *extra)

    def test_without_the_option_the_repair_runs(self):
        _, _, _, recover, repair = self.run_cli()
        recover.assert_not_called()
        repair.assert_called_once()


if __name__ == '__main__':
    unittest.main()
