import io
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool import cli, task_completion, task_submission_cli


TASK_ID = 'a' * 32
RUN_ID = 'b' * 32
CONTRACT = 'c' * 64
HEAD = 'd' * 40
SOURCE = 'e' * 40
COMMON = '/repo/.git'


class FakeStore:
    def __init__(self, *, binding=True, completed=True):
        evidence = {'common_dir': COMMON}
        if completed:
            evidence['result'] = {'result_head': HEAD, 'source_sha': SOURCE}
        self.task = {
            'task_id': TASK_ID,
            'run_id': RUN_ID,
            'task_binding': ({'task_id': TASK_ID, 'run_id': RUN_ID,
                              'contract_digest': CONTRACT} if binding else None),
            'completion': {
                'executor_task_id': 'executor-d6',
                'evidence': evidence,
            },
        }

    def get(self, task_id):
        if task_id != TASK_ID:
            raise AssertionError('unexpected task id')
        return self.task


def registration(task_id):
    return SimpleNamespace(task_id=task_id, root='/repo', common_dir=COMMON,
                           branch='task-branch', source_sha=SOURCE, result_sha=HEAD)


def snapshot(head=HEAD):
    return SimpleNamespace(root='/repo', common_dir=COMMON,
                           branch='task-branch', head=head)


class TaskReviewPromptTests(unittest.TestCase):
    def invoke(self, *, store=None, snapshots=None, pr_head=HEAD):
        args = Namespace(task_id=TASK_ID, executor_task='executor-d6',
                         reviewer_task='reviewer-d6', pr=42)
        output = io.StringIO()
        registrations = {'executor-d6': registration('executor-d6'),
                          'reviewer-d6': registration('reviewer-d6')}
        if snapshots is None:
            snapshots = [snapshot(), snapshot()]
        with patch.object(task_completion, 'read_d6_registration',
                          side_effect=lambda task_id, *_a, **_kw: registrations[task_id]), \
                patch.object(task_completion, 'collect_git_snapshot', side_effect=snapshots), \
                patch.object(task_completion, '_github_get', return_value={
                    'number': 42, 'head': {'sha': pr_head}}), \
                redirect_stdout(output):
            code = task_submission_cli.run_task_review_prompt(args, store=store or FakeStore())
        return code, output.getvalue()

    def test_success_prints_filled_exact_review_lines(self):
        code, output = self.invoke()
        binding = (f'LLMTOOL-REVIEW-BINDING v1 task={TASK_ID} run={RUN_ID} '
                   f'contract={CONTRACT} head={HEAD}')
        self.assertEqual(code, 0)
        self.assertIn(f'Review PR #42 at exact HEAD {HEAD}.', output)
        self.assertIn(f'Scope: git diff {SOURCE}..{HEAD}', output)
        self.assertIn(task_completion._REVIEW_VERDICT_ACTIVITY_LINE, output)
        self.assertIn(f'Verdict: PASS | Version: v1 | HEAD: {HEAD}', output)
        self.assertIn(task_completion._FORMAL_ACCEPT, output)
        self.assertIn(binding, output)

    def test_success_before_task_completion_uses_d6_result_and_source(self):
        code, output = self.invoke(store=FakeStore(completed=False))
        self.assertEqual(code, 0)
        self.assertIn(f'Review PR #42 at exact HEAD {HEAD}.', output)
        self.assertIn(f'Scope: git diff {SOURCE}..{HEAD}', output)

    def test_refuses_unrecorded_executor_head_without_prompt(self):
        with self.assertRaises(LLMToolError) as raised:
            self.invoke(snapshots=[snapshot('f' * 40), snapshot()])
        self.assertEqual(raised.exception.completion_code,
                         'REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')

    def test_refuses_reviewer_head_mismatch_without_prompt(self):
        with self.assertRaises(LLMToolError) as raised:
            self.invoke(snapshots=[snapshot(), snapshot('f' * 40)])
        self.assertEqual(raised.exception.completion_code,
                         'REVIEW_PROMPT_REVIEWER_HEAD_MISMATCH')

    def test_refuses_pr_head_mismatch_without_prompt(self):
        with self.assertRaises(LLMToolError) as raised:
            self.invoke(pr_head='f' * 40)
        self.assertEqual(raised.exception.completion_code,
                         'REVIEW_PROMPT_PR_HEAD_MISMATCH')

    def test_refuses_missing_task_binding_without_prompt(self):
        args = Namespace(task_id=TASK_ID, executor_task='executor-d6',
                         reviewer_task='reviewer-d6', pr=42)
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(LLMToolError) as raised:
            task_submission_cli.run_task_review_prompt(args, store=FakeStore(binding=False))
        self.assertEqual(raised.exception.completion_code,
                         'REVIEW_PROMPT_BINDING_MISSING')
        self.assertEqual(output.getvalue(), '')

    def test_cli_prints_each_refusal_code(self):
        codes = ('REVIEW_PROMPT_BINDING_MISSING',
                 'REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH',
                 'REVIEW_PROMPT_REVIEWER_HEAD_MISMATCH',
                 'REVIEW_PROMPT_PR_HEAD_MISMATCH',
                 'REVIEW_PROMPT_D6_UNAVAILABLE',
                 'REVIEW_PROMPT_PR_UNAVAILABLE')
        for code in codes:
            error = LLMToolError('Reviewer prompt is unavailable; details omitted')
            error.completion_code = code
            output = io.StringIO()
            with self.subTest(code=code), patch.object(
                    cli, 'run_task_review_prompt', side_effect=error), \
                    redirect_stderr(output):
                self.assertEqual(cli.main(['task', 'review-prompt', TASK_ID,
                                           '--executor-task', 'executor-d6',
                                           '--reviewer-task', 'reviewer-d6', '--pr', '42']), 1)
            self.assertIn(f'llmtool: review-prompt refused: {code}', output.getvalue())


if __name__ == '__main__':
    unittest.main()
