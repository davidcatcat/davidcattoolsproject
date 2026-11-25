"""#2736: an empty OpenCode diff with a reply is a responded run."""

import hashlib
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import review_trust_fixture
import test_task_submission_cli as base
import worker_policy_fixture
from llmtool.opencode_direct_developer import DirectResult
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import run_task_delegate


def setUpModule():
    review_trust_fixture.start_module()
    worker_policy_fixture.start_module()


def tearDownModule():
    worker_policy_fixture.stop_module()
    review_trust_fixture.stop_module()


def _result(text, *, session='ses_Abc123'):
    return DirectResult(0, session, text, None, 'UNKNOWN', 'a' * 64, 32,
                        'CONFIRMED', 'opencode v2.0.32', 10, 1, 'b' * 64)


class OpenCodeEmptyReply2736Tests(unittest.TestCase):
    # Reuse the real OpenCode delegate fixtures (D6 registry, TaskStore, PM
    # task) without re-collecting that module's test methods.
    setUp = base.TaskSubmissionCLITests.setUp
    invoke = base.TaskSubmissionCLITests.invoke
    _pm_task_with_opencode_assignment = (
        base.TaskSubmissionCLITests._pm_task_with_opencode_assignment)
    _opencode_delegate_args = base.TaskSubmissionCLITests._opencode_delegate_args

    def _store(self):
        return TaskStore(Path(self.home) / '.local/state/llmtool/tasks')

    def _run(self, result):
        task = self._pm_task_with_opencode_assignment()
        args = self._opencode_delegate_args(task)
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                      return_value=result), \
                patch('llmtool.task_submission_cli.run_task_direct_result_event',
                      return_value=0):
            code = run_task_delegate(args)
        return code, self._store().get(task['task_id'])

    def test_empty_diff_with_reply_responds_with_empty_artifact_2736(self):
        reply = ("I'll start by reading the relevant files and then make the change.\n"
                 'SPEC_MISMATCH: acceptance A2 needs a path outside read_paths.')
        code, record = self._run(_result(reply))
        developer = record['developer']
        self.assertEqual(code, 0)
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(developer['artifact'], '')
        self.assertEqual(developer['artifact_sha256'],
                         hashlib.sha256(b'').hexdigest())
        self.assertIsNone(developer['error_code'])
        self.assertEqual(developer['reported']['summary'], reply)

    def test_empty_diff_with_empty_reply_keeps_envelope_rejection_2736(self):
        for reply in ('', '   \n'):
            with self.subTest(reply=reply):
                code, record = self._run(_result(reply))
                developer = record['developer']
                self.assertEqual(code, 1)
                self.assertEqual(developer['state'], 'DEVELOPER_OUTPUT_REJECTED')
                self.assertEqual(developer['error_code'],
                                 'DEVELOPER_OUTPUT_INVALID_ENVELOPE')

    def test_non_empty_out_of_scope_diff_stays_out_of_scope_2736(self):
        task = self._pm_task_with_opencode_assignment()
        args = self._opencode_delegate_args(task)
        reply = "I'll start by reading the relevant files and then make the change."

        def execute(**_kwargs):
            (self.executor_root / 'README.md').write_text(
                'Executor fixture documentation changed.\n')
            return _result(reply)

        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                      side_effect=execute), \
                patch('llmtool.task_submission_cli.run_task_direct_result_event',
                      return_value=0):
            code = run_task_delegate(args)
        developer = self._store().get(task['task_id'])['developer']
        self.assertEqual(code, 1)
        self.assertEqual(developer['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(developer['error_code'],
                         'DEVELOPER_OUTPUT_OUT_OF_SCOPE')


if __name__ == '__main__':
    unittest.main()
