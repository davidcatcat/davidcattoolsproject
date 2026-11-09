import hashlib
import json
from types import SimpleNamespace
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


def task_with_module_count(count):
    modules = [f'test_gate_sample_{index}' for index in range(count)]
    task = task_with_tests()
    task['task_contract']['test_commands'] = [
        {'argv': ['python3', '-m', 'unittest', 'discover', '-s',
                  'tools/llmtool/tests', '-p', module + '.py'], 'cwd': '.'}
        for module in modules]
    return task, ['tools/llmtool/tests/' + module + '.py' for module in modules]


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

        def unconfigured():
            raise FileNotFoundError

        self.assertEqual(task_submission_cli._remote_receipt_test_policy(
            task, read_paths=[PATH], config_loader=unconfigured), 'REMOTE_RECEIPT')
        self.assertEqual(task_submission_cli._remote_receipt_test_policy(
            task, read_paths=[PATH],
            config_loader=lambda: (_ for _ in ()).throw(
                LLMToolError('REMOTE_TEST_CONFIG_INVALID'))), 'REMOTE_RECEIPT')
        outside = task_with_tests()
        outside['task_contract']['test_commands'][0]['argv'][-1] = 'test_other.py'
        self.assertEqual(task_submission_cli._remote_receipt_test_policy(
            outside, read_paths=[PATH], config_loader=lambda: {}), 'LOCAL')
        non_unittest = task_with_tests()
        non_unittest['task_contract']['test_commands'][0]['argv'] = ['python3', 'script.py']
        self.assertEqual(task_submission_cli._remote_receipt_test_policy(
            non_unittest, read_paths=[PATH], config_loader=lambda: {}), 'LOCAL')

    def test_eligibility_caps_remote_modules_at_executor_limit(self):
        eight_module_task, eight_module_paths = task_with_module_count(8)
        self.assertEqual(task_submission_cli._remote_receipt_test_policy(
            eight_module_task, read_paths=eight_module_paths,
            config_loader=lambda: {}), 'REMOTE_RECEIPT')

        nine_module_task, nine_module_paths = task_with_module_count(9)
        self.assertEqual(task_submission_cli._remote_receipt_test_policy(
            nine_module_task, read_paths=nine_module_paths,
            config_loader=lambda: {}), 'LOCAL')

    def test_prompt_includes_remote_instruction_and_preserves_local_instruction(self):
        task = task_with_tests()
        task['task_binding'] = {'contract_digest': '3' * 64}
        common = dict(source_sha=SHA, read_paths=[PATH], write_paths=['tools/llmtool/a.py'],
                      acceptance=['done'], pm_goal='goal', model='gpt-6-luna',
                      reasoning_effort='high')
        with patch.object(remote_test, '_config', return_value={}):
            remote = task_submission_cli._codex_direct_prompt(task, **common)
        self.assertIn('Do not run unittest in the sandbox.', remote)
        self.assertIn('"test_policy":"REMOTE_RECEIPT"', remote)
        self.assertNotIn('Run only listed test_commands', remote)
        self.assertNotIn('Run only listed test_commands;', remote)

        with patch('llmtool.task_submission_cli._remote_receipt_test_policy',
                   return_value='LOCAL'):
            local = task_submission_cli._codex_direct_prompt(task, **common)
        self.assertIn('Run only listed test_commands;', local)
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

    def test_remote_testable_task_with_missing_config_defers_remote_tests_and_skips_local(self):
        task = task_with_tests()
        task['developer'] = {'read_paths': [PATH], 'write_paths': [PATH]}

        def unconfigured():
            raise FileNotFoundError

        with patch.object(remote_test, '_config', side_effect=unconfigured):
            policy = task_submission_cli._remote_receipt_test_policy(
                task, read_paths=[PATH])
            self.assertEqual(policy, 'REMOTE_RECEIPT')

            registration = SimpleNamespace(
                task_id='d6-task', trusted_agent_id='agent-1',
                session_ref='session-1', native_session_id='native-1',
                root='/repo', common_dir='/repo/.git', branch='feat/task')
            callback = task_submission_cli._direct_result_callback(
                task, registration, SHA)
            self.assertEqual(callback['evidence']['test_policy'], 'REMOTE_RECEIPT')

            result = {'diff_sha256': '4' * 64, 'changed_paths': [PATH]}
            with patch.object(task_submission_cli.task_completion,
                              'run_bounded_worktree_tests') as worktree_tests, \
                 patch.object(task_submission_cli.task_completion,
                              'run_bounded_tests') as bounded_tests:
                tests = task_submission_cli._direct_result_tests(
                    task, callback['evidence'], result, task['developer'])
                worktree_tests.assert_not_called()
                bounded_tests.assert_not_called()
            self.assertEqual(len(tests), 1)
            self.assertEqual(tests[0]['status'], 'DEFERRED_REMOTE_RECEIPT')
            self.assertEqual(tests[0]['path'], PATH)

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
            {'source_sha': 'c' * 40}, {'final': ['OK (skipping 1)']},
            {'final': ['OK (skipped=0)']},
        ]
        for change in mismatches:
            with self.subTest(change=change):
                candidate = sealed_receipt(**change)
                task['remote_test_receipts'] = [candidate]
                found, missing = task_submission_cli._matching_remote_test_receipt(
                    task, executor_task='d6-task', source_sha=SHA, paths=[PATH])
                self.assertIsNone(found)
                self.assertEqual(missing, 'MAC_EVIDENCE_REQUIRED'
                                 if 'skipp' in str(change).lower()
                                 else 'REMOTE_TEST_RECEIPT_MISSING')

    def test_gate_accepts_exact_skip_summary_and_rejects_other_skip_text(self):
        task = task_with_tests()
        task.update(task_id='1' * 32, run_id='2' * 32,
                    remote_test_receipts=[sealed_receipt(final=['OK (skipped=1)'])])

        receipt, code = task_submission_cli._matching_remote_test_receipt(
            task, executor_task='d6-task', source_sha=SHA, paths=[PATH])
        self.assertIsNotNone(receipt)
        self.assertIsNone(code)

        task['remote_test_receipts'] = [
            sealed_receipt(final=['OK (skipping 1)']),
            sealed_receipt(final=['OK (expected failures=1)']),
        ]
        receipt, code = task_submission_cli._matching_remote_test_receipt(
            task, executor_task='d6-task', source_sha=SHA, paths=[PATH])
        self.assertIsNone(receipt)
        self.assertEqual(code, 'MAC_EVIDENCE_REQUIRED')

    def test_consumer_rejects_receipt_for_sha_other_than_reviewed_head(self):
        task = task_with_tests()
        task.update(task_id='1' * 32, run_id='2' * 32,
                    remote_test_receipts=[sealed_receipt(source_sha='b' * 40)])

        receipt, code = task_submission_cli._matching_remote_test_receipt(
            task, executor_task='d6-task', source_sha=SHA, paths=[PATH])

        self.assertIsNone(receipt)
        self.assertEqual(code, 'REMOTE_TEST_RECEIPT_MISSING')


if __name__ == '__main__':
    unittest.main()
