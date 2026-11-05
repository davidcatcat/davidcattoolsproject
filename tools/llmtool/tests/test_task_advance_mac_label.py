import subprocess
import unittest
from unittest.mock import patch

from llmtool import task_advance
from test_task_advance import HEAD, PR, base_record, d6


class TaskAdvanceMacLabelTests(unittest.TestCase):
    def _advance_with_missing_gates(self, missing, label_result=None,
                                    label_error=None):
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1,
                 'state': 'COMPLETED', 'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}

        class Store:
            def __init__(self):
                self.record = base_record(review_dispatch_claim=[claim],
                                          completion_events=[event])

            def get(self, _task_id):
                return self.record

        store = Store()

        def complete(_args, *, store):
            store.record['completion']['missing_gates'] = missing
            return 0

        def label(**kwargs):
            self.label_kwargs = kwargs
            if label_error is not None:
                raise label_error
            return label_result

        get = lambda _path: PR
        runner = lambda *_args: subprocess.CompletedProcess([], 0)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance, 'label_mac_ci_for_skipped_check',
                          side_effect=label) as label_call:
            result = task_advance.advance(
                task_id=store.record['task_id'], store=store, pr_number=17,
                github_get=get, cwd='/repo/task', merge_gh_runner=runner,
                run_task_complete_call=complete)
        return result, label_call, store, get, runner

    def test_waiting_ci_label_result_appends_ready_code_and_uses_bound_arguments(self):
        result, label_call, store, get, runner = self._advance_with_missing_gates(
            ['CI_WORKFLOW_JOBS_INVALID'], {'step': 'WAITING_CI', 'codes': []})

        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertIn('MAC_CI_LABEL_READY', result['codes'])
        label_call.assert_called_once_with(task_id=store.record['task_id'],
            store=store, pr_number=17, github_get=get,
            merge_gh_runner=runner, cwd='/repo/task')

    def test_merge_refused_label_result_keeps_waiting_ci_and_appends_codes(self):
        result, label_call, *_ = self._advance_with_missing_gates(
            ['CI_WORKFLOW_JOBS_INVALID'],
            {'step': 'MERGE_REFUSED', 'codes': ['MERGE_MAC_CI_LABEL_FAILED']})

        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertIn('MERGE_MAC_CI_LABEL_FAILED', result['codes'])
        label_call.assert_called_once()

    def test_none_label_result_leaves_completion_codes_unchanged(self):
        result, label_call, *_ = self._advance_with_missing_gates(
            ['CI_WORKFLOW_JOBS_INVALID', 'REQUIRED_CI_FAILED'], None)

        self.assertEqual(result['step'], 'CI_FAILED')
        self.assertEqual(result['codes'], ['CI_WORKFLOW_JOBS_INVALID',
                                           'REQUIRED_CI_FAILED'])
        label_call.assert_called_once()

    def test_label_exception_keeps_waiting_ci_and_appends_failure_code(self):
        result, label_call, *_ = self._advance_with_missing_gates(
            ['CI_WORKFLOW_JOBS_INVALID'], label_error=RuntimeError('label failed'))

        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertEqual(result['codes'], ['CI_WORKFLOW_JOBS_INVALID',
                                           'MAC_CI_LABEL_FAILED'])
        label_call.assert_called_once()

    def test_other_missing_gates_do_not_call_mac_label_helper(self):
        result, label_call, *_ = self._advance_with_missing_gates(
            ['EXECUTOR_RESULT_PENDING'])

        self.assertEqual(result['step'], 'WAITING_EXECUTOR')
        label_call.assert_not_called()

    def test_label_helper_uses_only_the_mac_ci_pr_edit_arguments(self):
        record = base_record()

        class Store:
            def get(self, _task_id):
                return record

        current_pr = dict(PR, state='open', mergeable=True, labels=[])
        calls = []

        def runner(root, argv):
            calls.append((root, argv))
            return subprocess.CompletedProcess(argv, 0)

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=current_pr), \
             patch.object(task_advance, '_merge_checks',
                          return_value='MERGE_MACOS_CHECK_SKIPPED'):
            result = task_advance.label_mac_ci_for_skipped_check(
                task_id=record['task_id'], store=Store(), pr_number=17,
                github_get=lambda _path: current_pr,
                merge_gh_runner=runner, cwd='/repo/task')

        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertEqual(calls, [('/repo/task',
            ['pr', 'edit', '17', '--add-label', 'mac-ci'])])


if __name__ == '__main__':
    unittest.main()
