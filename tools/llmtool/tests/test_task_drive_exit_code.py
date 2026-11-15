import contextlib
import unittest
from unittest.mock import patch

from llmtool import pm_caller
from llmtool import task_submission_cli
from llmtool.cli import parser_for_cli

# #2073 pattern: task_drive fails closed (RESTART_REQUIRED) when source_freshness.code_tree()
# cannot read the package checkout, as on a non-git EC2 copy. Pin it for this module.
_code_tree_patch = None


def setUpModule():
    global _code_tree_patch
    _code_tree_patch = patch('llmtool.source_freshness.code_tree', return_value='d' * 64)
    _code_tree_patch.start()


def tearDownModule():
    global _code_tree_patch
    if _code_tree_patch is not None:
        _code_tree_patch.stop()


class StubStore:
    def __init__(self, record=None):
        self._record = record or {
            'task_id': 'task-1',
            'lineage': None,
        }

    def read(self, task_id):
        return self._record

    def get(self, task_id, default=None):
        return self._record

    @contextlib.contextmanager
    def advance_lease(self, task_id):
        yield


class TaskDriveExitCodeTests(unittest.TestCase):
    def setUp(self):
        self.store = StubStore()

    def adapter(self, results):
        calls = []

        def advance(**kwargs):
            calls.append(kwargs)
            return results.pop(0)

        return advance, calls

    @patch('llmtool.task_submission_cli._print')
    @patch('llmtool.pm_caller.guarded', side_effect=lambda func, *a, **kw: func)
    @patch('llmtool.pm_caller.check_caller', return_value='test-caller-key')
    def test_ready_for_integration_with_waiting_integration_returns_zero(
            self, _check_caller, _guarded, _print):
        advance, _calls = self.adapter([
            {'task_id': 'task-1', 'step': 'READY_FOR_INTEGRATION',
             'codes': ['WAITING_INTEGRATION']},
        ])
        args = parser_for_cli().parse_args([
            'task', 'drive', 'task-1', '--poll-seconds', '0'])
        exit_code = task_submission_cli.run_task_drive(
            args, store=self.store, advance_call=advance, sleep=lambda _: None)
        self.assertEqual(exit_code, 0)

    @patch('llmtool.task_submission_cli._print')
    @patch('llmtool.pm_caller.guarded', side_effect=lambda func, *a, **kw: func)
    @patch('llmtool.pm_caller.check_caller', return_value='test-caller-key')
    def test_merged_with_merge_reconciled_returns_zero(
            self, _check_caller, _guarded, _print):
        advance, _calls = self.adapter([
            {'task_id': 'task-1', 'step': 'MERGED',
             'codes': ['MERGE_RECONCILED']},
        ])
        args = parser_for_cli().parse_args([
            'task', 'drive', 'task-1', '--poll-seconds', '0'])
        exit_code = task_submission_cli.run_task_drive(
            args, store=self.store, advance_call=advance, sleep=lambda _: None)
        self.assertEqual(exit_code, 0)

    @patch('llmtool.task_submission_cli._print')
    @patch('llmtool.pm_caller.guarded', side_effect=lambda func, *a, **kw: func)
    @patch('llmtool.pm_caller.check_caller', return_value='test-caller-key')
    def test_no_codes_returns_zero(
            self, _check_caller, _guarded, _print):
        advance, _calls = self.adapter([
            {'task_id': 'task-1', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
        ])
        args = parser_for_cli().parse_args([
            'task', 'drive', 'task-1', '--poll-seconds', '0'])
        exit_code = task_submission_cli.run_task_drive(
            args, store=self.store, advance_call=advance, sleep=lambda _: None)
        self.assertEqual(exit_code, 0)

    @patch('llmtool.task_submission_cli._print')
    @patch('llmtool.pm_caller.guarded', side_effect=lambda func, *a, **kw: func)
    @patch('llmtool.pm_caller.check_caller', return_value='test-caller-key')
    def test_other_code_at_max_steps_returns_one(
            self, _check_caller, _guarded, _print):
        advance, _calls = self.adapter([
            {'task_id': 'task-1', 'step': 'WAITING_CI',
             'codes': ['LINUX_CI_PENDING']},
        ])
        args = parser_for_cli().parse_args([
            'task', 'drive', 'task-1', '--max-steps', '1', '--poll-seconds', '0'])
        exit_code = task_submission_cli.run_task_drive(
            args, store=self.store, advance_call=advance, sleep=lambda _: None)
        self.assertEqual(exit_code, 1)

    @patch('llmtool.task_submission_cli._print')
    @patch('llmtool.pm_caller.check_caller',
           side_effect=pm_caller.PMCallerRefused('NOT_OWNER'))
    def test_pm_caller_refusal_returns_one(self, _check_caller, _print):
        advance, _calls = self.adapter([])
        args = parser_for_cli().parse_args([
            'task', 'drive', 'task-1', '--poll-seconds', '0'])
        exit_code = task_submission_cli.run_task_drive(
            args, store=self.store, advance_call=advance, sleep=lambda _: None)
        self.assertEqual(exit_code, 1)

    @patch('llmtool.task_submission_cli._print')
    @patch('llmtool.pm_caller.guarded', side_effect=lambda func, *a, **kw: func)
    @patch('llmtool.pm_caller.check_caller', return_value='test-caller-key')
    @patch('llmtool.task_drive.drive')
    def test_step_refused_with_empty_codes_returns_one(
            self, mock_drive, _check_caller, _guarded, _print):
        mock_drive.return_value = {
            'task_id': 'task-1', 'step': 'REFUSED', 'codes': []}
        advance, _calls = self.adapter([])
        args = parser_for_cli().parse_args([
            'task', 'drive', 'task-1', '--poll-seconds', '0'])
        exit_code = task_submission_cli.run_task_drive(
            args, store=self.store, advance_call=advance, sleep=lambda _: None)
        self.assertEqual(exit_code, 1)

    @patch('llmtool.task_submission_cli._print')
    @patch('llmtool.pm_caller.guarded', side_effect=lambda func, *a, **kw: func)
    @patch('llmtool.pm_caller.check_caller', return_value='test-caller-key')
    @patch('llmtool.task_drive.drive')
    def test_step_max_steps_with_empty_codes_returns_one(
            self, mock_drive, _check_caller, _guarded, _print):
        mock_drive.return_value = {
            'task_id': 'task-1', 'step': 'MAX_STEPS', 'codes': []}
        advance, _calls = self.adapter([])
        args = parser_for_cli().parse_args([
            'task', 'drive', 'task-1', '--poll-seconds', '0'])
        exit_code = task_submission_cli.run_task_drive(
            args, store=self.store, advance_call=advance, sleep=lambda _: None)
        self.assertEqual(exit_code, 1)

    @patch('llmtool.task_submission_cli._print')
    @patch('llmtool.pm_caller.guarded', side_effect=lambda func, *a, **kw: func)
    @patch('llmtool.pm_caller.check_caller', return_value='test-caller-key')
    @patch('llmtool.task_drive.drive')
    def test_step_unknown_with_empty_codes_returns_one(
            self, mock_drive, _check_caller, _guarded, _print):
        mock_drive.return_value = {
            'task_id': 'task-1', 'step': 'UNKNOWN', 'codes': []}
        advance, _calls = self.adapter([])
        args = parser_for_cli().parse_args([
            'task', 'drive', 'task-1', '--poll-seconds', '0'])
        exit_code = task_submission_cli.run_task_drive(
            args, store=self.store, advance_call=advance, sleep=lambda _: None)
        self.assertEqual(exit_code, 1)

    @patch('llmtool.task_submission_cli._print')
    @patch('llmtool.pm_caller.guarded', side_effect=lambda func, *a, **kw: func)
    @patch('llmtool.pm_caller.check_caller', return_value='test-caller-key')
    @patch('llmtool.task_drive.drive')
    def test_step_reviewer_attention_with_empty_codes_returns_one(
            self, mock_drive, _check_caller, _guarded, _print):
        mock_drive.return_value = {
            'task_id': 'task-1', 'step': 'REVIEWER_ATTENTION', 'codes': []}
        advance, _calls = self.adapter([])
        args = parser_for_cli().parse_args([
            'task', 'drive', 'task-1', '--poll-seconds', '0'])
        exit_code = task_submission_cli.run_task_drive(
            args, store=self.store, advance_call=advance, sleep=lambda _: None)
        self.assertEqual(exit_code, 1)

    @patch('llmtool.task_submission_cli._print')
    @patch('llmtool.pm_caller.guarded', side_effect=lambda func, *a, **kw: func)
    @patch('llmtool.pm_caller.check_caller', return_value='test-caller-key')
    @patch('llmtool.task_drive.drive')
    def test_step_merged_with_empty_codes_returns_zero(
            self, mock_drive, _check_caller, _guarded, _print):
        mock_drive.return_value = {
            'task_id': 'task-1', 'step': 'MERGED', 'codes': []}
        advance, _calls = self.adapter([])
        args = parser_for_cli().parse_args([
            'task', 'drive', 'task-1', '--poll-seconds', '0'])
        exit_code = task_submission_cli.run_task_drive(
            args, store=self.store, advance_call=advance, sleep=lambda _: None)
        self.assertEqual(exit_code, 0)

    @patch('llmtool.gw_agent_sessions.sweep_quietly')
    @patch('llmtool.task_submission_cli._print')
    @patch('llmtool.pm_caller.guarded', side_effect=lambda func, *a, **kw: func)
    @patch('llmtool.pm_caller.check_caller', return_value='test-caller-key')
    @patch('llmtool.task_drive.drive')
    def test_step_closed_with_empty_codes_returns_zero(
            self, mock_drive, _check_caller, _guarded, _print, _sweep_quietly):
        mock_drive.return_value = {
            'task_id': 'task-1', 'step': 'CLOSED', 'codes': []}
        advance, _calls = self.adapter([])
        args = parser_for_cli().parse_args([
            'task', 'drive', 'task-1', '--poll-seconds', '0'])
        exit_code = task_submission_cli.run_task_drive(
            args, store=self.store, advance_call=advance, sleep=lambda _: None)
        self.assertEqual(exit_code, 0)

    @patch('llmtool.task_submission_cli._print')
    @patch('llmtool.pm_caller.guarded', side_effect=lambda func, *a, **kw: func)
    @patch('llmtool.pm_caller.check_caller', return_value='test-caller-key')
    @patch('llmtool.task_drive.drive')
    def test_step_max_steps_with_no_codes_key_returns_one(
            self, mock_drive, _check_caller, _guarded, _print):
        mock_drive.return_value = {
            'task_id': 'task-1', 'step': 'MAX_STEPS'}
        advance, _calls = self.adapter([])
        args = parser_for_cli().parse_args([
            'task', 'drive', 'task-1', '--poll-seconds', '0'])
        exit_code = task_submission_cli.run_task_drive(
            args, store=self.store, advance_call=advance, sleep=lambda _: None)
        self.assertEqual(exit_code, 1)

    def test_advance_exit_code_empty_codes_at_max_steps_returns_zero(self):
        self.assertEqual(
            task_submission_cli._advance_exit_code({'step': 'MAX_STEPS', 'codes': []}),
            0)
