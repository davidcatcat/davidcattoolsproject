"""Contract tests for ``llmtool task delegate`` completion-code refusal output."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from llmtool import cli
from llmtool.errors import LLMToolError


_UNAVAILABLE_MSG = 'Executor completion evidence is unavailable; details omitted'


class CliDelegateRefusalCodeTests(unittest.TestCase):
    def _run_task_action(self, action, error):
        args = SimpleNamespace(command='task', action=action)
        stdout = StringIO()
        stderr = StringIO()
        function_name, needs_fresh_source = cli.TASK_ACTIONS.get(action, (None, False))
        patches = [
            patch.object(cli, 'parser_for_cli',
                         return_value=SimpleNamespace(parse_args=lambda _argv: args)),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ]
        if needs_fresh_source:
            patches.append(patch('llmtool.source_freshness.require_fresh_source'))
        if function_name is not None:
            patches.append(patch.object(cli.task_submission_cli, function_name, side_effect=error))
        elif action == 'review':
            patches.append(patch.object(cli, 'run_task_review', side_effect=error))
        else:
            patches.append(patch.object(cli, 'run_task_inspect', side_effect=error))
        with ExitStack() as stack:
            for cm in patches:
                stack.enter_context(cm)
            exit_code = cli.main([])
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_delegate_refusal_prints_valid_completion_code(self):
        for code in ('D6_SOURCE_BINDING_MISMATCH', 'A', 'A' * 64, '0_A_9'):
            with self.subTest(code=code):
                error = LLMToolError(_UNAVAILABLE_MSG)
                error.completion_code = code
                exit_code, stdout, stderr = self._run_task_action('delegate', error)
                self.assertEqual(exit_code, 1)
                self.assertEqual(stdout, '')
                self.assertEqual(stderr, f'llmtool: delegate refused: {code}\n')

    def test_delegate_refusal_keeps_existing_output_when_code_missing(self):
        for has_attr in (False, True):
            with self.subTest(has_attr=has_attr):
                error = LLMToolError(_UNAVAILABLE_MSG)
                if has_attr:
                    error.completion_code = None
                exit_code, stdout, stderr = self._run_task_action('delegate', error)
                self.assertEqual(exit_code, 1)
                self.assertEqual(stdout, '')
                self.assertEqual(stderr, f'llmtool: {_UNAVAILABLE_MSG}\n')

    def test_delegate_refusal_keeps_existing_output_for_non_matching_codes(self):
        invalid_codes = ('', 'A' * 65, 'lowercase', 'BAD-CODE', 'BAD CODE', 'BAD_CODE\n', 123, True)
        for code in invalid_codes:
            with self.subTest(code=repr(code)):
                error = LLMToolError(_UNAVAILABLE_MSG)
                error.completion_code = code
                exit_code, stdout, stderr = self._run_task_action('delegate', error)
                self.assertEqual(exit_code, 1)
                self.assertEqual(stdout, '')
                self.assertEqual(stderr, f'llmtool: {_UNAVAILABLE_MSG}\n')

    def test_other_task_actions_are_unchanged(self):
        complete_error = LLMToolError(_UNAVAILABLE_MSG)
        complete_error.completion_code = 'D6_SOURCE_BINDING_MISMATCH'
        exit_code, stdout, stderr = self._run_task_action('complete', complete_error)
        self.assertEqual((exit_code, stdout, stderr), (1, '', f'llmtool: {_UNAVAILABLE_MSG}\n'))

        prompt_error = LLMToolError(_UNAVAILABLE_MSG)
        prompt_error.completion_code = 'REVIEW_PROMPT_CODE'
        exit_code, stdout, stderr = self._run_task_action('review-prompt', prompt_error)
        self.assertEqual((exit_code, stdout, stderr),
                         (1, '', 'llmtool: review-prompt refused: REVIEW_PROMPT_CODE\n'))

        repair_error = LLMToolError(_UNAVAILABLE_MSG)
        repair_error.review_repair_code = 'REVIEW_REPAIR_REFUSED'
        exit_code, stdout, stderr = self._run_task_action('repair', repair_error)
        self.assertEqual((exit_code, stdout, stderr),
                         (1, '', 'llmtool: repair refused: REVIEW_REPAIR_REFUSED\n'))


if __name__ == '__main__':
    unittest.main()
