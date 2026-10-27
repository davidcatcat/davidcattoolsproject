import hashlib
import json
import unittest
from unittest.mock import patch

from llmtool import remote_test, task_submission_cli
from llmtool.errors import LLMToolError
from llmtool.task_store import _json_bytes


SHA = 'a' * 40
MODULE = 'test_gate_sample'
PATH = 'tools/llmtool/tests/' + MODULE + '.py'


def task_with_tests():
    return {
        'task_id': '1' * 32, 'run_id': '2' * 32,
        'task_binding': {'contract_digest': '3' * 64},
        'task_contract': {'test_commands': [{
            'argv': ['python3', '-m', 'unittest', 'discover', '-s',
                     'tools/llmtool/tests', '-p', MODULE + '.py'], 'cwd': '.'}]},
    }


def sealed_receipt(**changes):
    value = {
        'task_id': '1' * 32, 'run_id': '2' * 32,
        'executor_task': 'd6-task', 'source_sha': SHA, 'result': 'PASS',
        'modules': [MODULE], 'ran_total': 2, 'failures': [], 'final': ['OK'],
    }
    value.update(changes)
    value['receipt_digest'] = hashlib.sha256(_json_bytes(value)).hexdigest()
    return value


class RemoteTestGateTests(unittest.TestCase):
    def test_eligibility_requires_remote_config_and_admitted_task_modules(self):
        task = task_with_tests()
        self.assertEqual(task_submission_cli._remote_receipt_test_policy(
            task, read_paths=[PATH], config_loader=lambda: {}), 'REMOTE_RECEIPT')

        def unavailable():
            raise LLMToolError('unavailable')

        self.assertEqual(task_submission_cli._remote_receipt_test_policy(
            task, read_paths=[PATH], config_loader=unavailable), 'LOCAL')
        outside = task_with_tests()
        outside['task_contract']['test_commands'][0]['argv'][-1] = 'test_other.py'
        self.assertEqual(task_submission_cli._remote_receipt_test_policy(
            outside, read_paths=[PATH], config_loader=lambda: {}), 'LOCAL')
        non_unittest = task_with_tests()
        non_unittest['task_contract']['test_commands'][0]['argv'] = ['python3', 'script.py']
        self.assertEqual(task_submission_cli._remote_receipt_test_policy(
            non_unittest, read_paths=[PATH], config_loader=lambda: {}), 'LOCAL')

    def test_prompt_includes_remote_instruction_and_preserves_local_instruction(self):
        task = task_with_tests()
        task['task_binding'] = {'contract_digest': '3' * 64}
        common = dict(source_sha=SHA, read_paths=[PATH], write_paths=['tools/llmtool/a.py'],
                      acceptance=['done'], pm_goal='goal', model='gpt-6-luna',
                      reasoning_effort='high')
        with patch.object(remote_test, '_config', return_value={}):
            remote = task_submission_cli._codex_direct_prompt(task, **common)
        self.assertIn('Do not run unittest suites in the sandbox.', remote)
        self.assertIn('"test_policy":"REMOTE_RECEIPT"', remote)
        self.assertNotIn('Run the test_commands listed in the Task Contract', remote)

        def unavailable():
            raise LLMToolError('unavailable')

        with patch.object(remote_test, '_config', side_effect=unavailable):
            local = task_submission_cli._codex_direct_prompt(task, **common)
        self.assertIn('Run the test_commands listed in the Task Contract;', local)
        self.assertIn('"test_policy":"LOCAL"', local)

    def test_direct_callback_defers_remote_tests_and_keeps_local_execution(self):
        task = task_with_tests()
        evidence = {'test_policy': 'REMOTE_RECEIPT', 'source_sha': SHA,
                    'root': '/repo'}
        result = {'diff_sha256': '4' * 64, 'changed_paths': [PATH]}
        developer = {'read_paths': [PATH], 'write_paths': [PATH]}
        with patch.object(task_submission_cli.task_completion,
                          'run_bounded_worktree_tests') as local_tests:
            deferred = task_submission_cli._direct_result_tests(
                task, evidence, result, developer)
        local_tests.assert_not_called()
        self.assertEqual(deferred[0]['status'], 'DEFERRED_REMOTE_RECEIPT')

        evidence['test_policy'] = 'LOCAL'
        with patch.object(task_submission_cli.task_completion,
                          'run_bounded_worktree_tests', return_value=[{'status': 'PASSED'}]) as local_tests:
            self.assertEqual(task_submission_cli._direct_result_tests(
                task, evidence, result, developer), [{'status': 'PASSED'}])
        local_tests.assert_called_once()

    def test_gate_accepts_only_exact_bound_verified_pass_receipt(self):
        task = task_with_tests()
        task.update(task_id='1' * 32, run_id='2' * 32,
                    remote_test_receipts=[sealed_receipt()])
        receipt, code = task_submission_cli._matching_remote_test_receipt(
            task, executor_task='d6-task', source_sha=SHA, paths=[PATH])
        self.assertIsNotNone(receipt)
        self.assertIsNone(code)

        mismatches = [
            {'task_id': 'x' * 32}, {'run_id': 'x' * 32},
            {'executor_task': 'other-task'}, {'source_sha': 'b' * 40},
            {'modules': ['test_other']}, {'ran_total': 0},
            {'failures': [MODULE]}, {'result': 'FAIL'},
            {'result': 'TIMEOUT'}, {'result': 'SHA_MISMATCH'},
            {'source_sha': 'c' * 40}, {'final': ['OK (skipped=1)']},
        ]
        for change in mismatches:
            with self.subTest(change=change):
                candidate = sealed_receipt(**change)
                task['remote_test_receipts'] = [candidate]
                found, missing = task_submission_cli._matching_remote_test_receipt(
                    task, executor_task='d6-task', source_sha=SHA, paths=[PATH])
                self.assertIsNone(found)
                self.assertEqual(missing, 'MAC_EVIDENCE_REQUIRED'
                                 if 'skipped' in str(change).lower()
                                 else 'REMOTE_TEST_RECEIPT_MISSING')


if __name__ == '__main__':
    unittest.main()
