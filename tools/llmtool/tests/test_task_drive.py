import unittest
from unittest.mock import patch

from llmtool import task_drive
from llmtool import task_advance
from llmtool import task_submission_cli
from llmtool.cli import parser_for_cli


class TaskDriveTests(unittest.TestCase):
    def adapter(self, results):
        calls = []

        def advance(**kwargs):
            calls.append(kwargs)
            return results.pop(0)

        return advance, calls

    def quota_ok(self):
        return {
            'core': {'remaining': 5000, 'reset': 1700000100},
            'graphql': {'remaining': 5000, 'reset': 1700000200},
        }

    def test_drives_each_step_and_merges_only_when_requested(self):
        steps = [
            {'task_id': 't', 'pr': 17, 'head': 'a' * 40, 'codes': []},
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST',
             'result': {'result': 'PASS'}, 'codes': []},
            {'task_id': 't', 'step': 'DISPATCH_REVIEW', 'codes': []},
            {'task_id': 't', 'step': 'RECORD_REVIEW_RESULT',
             'result': {'outcome': 'REVIEW_PASS'}, 'codes': []},
            {'task_id': 't', 'step': 'RUN_TASK_COMPLETE', 'codes': []},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION',
             'codes': ['WAITING_INTEGRATION']},
            {'task_id': 't', 'step': 'MERGED', 'codes': []},
        ]
        advance, calls = self.adapter(steps)
        result = task_drive.drive(task_id='t', merge=True, max_steps=7,
                                  advance_call=advance)
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual([call['merge'] for call in calls], [False] * 6 + [True])

    def test_carries_published_pr_into_later_advance_calls(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'pr': 17, 'head': 'a' * 40, 'codes': []},
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST', 'codes': []},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
        ])

        result = task_drive.drive(task_id='t', advance_call=advance)

        self.assertEqual(result['step'], 'READY_FOR_INTEGRATION')
        self.assertIsNone(calls[0]['pr_number'])
        self.assertEqual([call['pr_number'] for call in calls[1:]], [17, 17])

    def test_explicit_pr_number_is_not_replaced_by_published_pr(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'pr': 17, 'head': 'a' * 40, 'codes': []},
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST', 'codes': []},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
        ])

        result = task_drive.drive(task_id='t', pr_number=23,
                                  advance_call=advance)

        self.assertEqual(result['step'], 'READY_FOR_INTEGRATION')
        self.assertEqual([call['pr_number'] for call in calls], [23, 23, 23])

    def test_cli_registers_foreground_drive_options(self):
        args = parser_for_cli().parse_args([
            'task', 'drive', 'task-1', '--merge', '--max-steps', '12',
            '--poll-seconds', '0.25', '--pr', '17', '--integration-pr', '18',
            '--reviewer-session-done'])
        self.assertEqual(args.action, 'drive')
        self.assertTrue(args.merge)
        self.assertEqual(args.max_steps, 12)
        self.assertEqual(args.poll_seconds, 0.25)
        self.assertEqual(args.pr, 17)
        self.assertEqual(args.integration_pr, 18)
        self.assertTrue(args.reviewer_session_done)

    def test_runs_repair_and_republish_steps_in_sequence(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'REPAIR_REVIEW', 'codes': []},
            {'task_id': 't', 'pr': 17, 'head': 'b' * 40, 'codes': []},
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST', 'codes': []},
            {'task_id': 't', 'step': 'DISPATCH_REVIEW', 'codes': []},
            {'task_id': 't', 'step': 'RECORD_REVIEW_RESULT', 'codes': []},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION',
             'codes': ['WAITING_INTEGRATION']},
        ])
        result = task_drive.drive(task_id='t', advance_call=advance)
        self.assertEqual(result['step'], 'READY_FOR_INTEGRATION')
        self.assertEqual(len(calls), 6)

    def test_waits_for_ci_and_executor_then_stops_on_refusal(self):
        pauses = []
        advance, _calls = self.adapter([
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']},
            {'task_id': 't', 'step': 'WAITING_EXECUTOR', 'codes': ['WAITING_EXECUTOR']},
            {'task_id': 't', 'step': 'REFUSED', 'codes': ['PUBLISH_REFUSED']},
        ])
        result = task_drive.drive(task_id='t', poll_seconds=1,
                                  advance_call=advance, quota_read=self.quota_ok,
                                  sleep=pauses.append)
        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(pauses, [1, 2])

    def test_pending_review_not_ready_waits_without_counting_a_workflow_step(self):
        pauses = []
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'RECORD_REVIEW_RESULT',
             'result': {'state': 'NOT_READY'}, 'codes': []},
            {'task_id': 't', 'step': 'RECORD_REVIEW_RESULT',
             'result': {'outcome': 'REVIEW_PASS'}, 'codes': []},
            {'task_id': 't', 'step': 'RUN_TASK_COMPLETE', 'codes': []},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION',
             'codes': ['WAITING_INTEGRATION']},
        ])
        result = task_drive.drive(task_id='t', poll_seconds=0.25,
                                  advance_call=advance, quota_read=self.quota_ok,
                                  sleep=pauses.append)
        self.assertEqual(result['step'], 'READY_FOR_INTEGRATION')
        self.assertEqual(result['steps'], 3)
        self.assertEqual(len(calls), 4)
        self.assertEqual(pauses, [0.25])

    def test_ready_integration_stops_or_merges_on_request(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION',
             'codes': ['WAITING_INTEGRATION']},
            {'task_id': 't', 'step': 'MERGED', 'codes': []},
        ])
        result = task_drive.drive(task_id='t', merge=True, advance_call=advance)
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(result['steps'], 2)
        self.assertEqual([call['merge'] for call in calls], [False, True])

    def test_merge_refusal_returns_immediately_after_mac_ci_becomes_pending(self):
        pauses = []
        calls = []
        state = {'mac_ci_labels': 0, 'merge_polls': 0}

        def advance(**kwargs):
            calls.append(kwargs)
            if not kwargs['merge']:
                # This is the real non-merge result even after Mac CI completes.
                return {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []}
            state['merge_polls'] += 1
            if state['merge_polls'] == 1:
                state['mac_ci_labels'] += 1
                return {'task_id': 't', 'step': 'WAITING_CI', 'codes': []}
            if state['merge_polls'] == 2:
                return {'task_id': 't', 'step': 'MERGE_REFUSED',
                        'codes': ['MERGE_CHECKS_NOT_GREEN'],
                        'result': {'macos_check_pending': True}}
            if state['merge_polls'] == 3:
                return {'task_id': 't', 'step': 'MERGE_REFUSED',
                        'codes': ['MERGE_MACOS_CHECK_REQUIRED']}
            return {'task_id': 't', 'step': 'MERGED', 'codes': []}

        result = task_drive.drive(task_id='t', merge=True, max_steps=5,
                                  poll_seconds=0.25, advance_call=advance,
                                  quota_read=self.quota_ok, sleep=pauses.append)

        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_CHECKS_NOT_GREEN'])
        self.assertEqual([call['merge'] for call in calls], [False, True, True])
        self.assertEqual(state['mac_ci_labels'], 1)
        self.assertEqual(pauses, [0.25])

    def test_merge_uses_existing_path_for_skipped_mac_completion_evidence(self):
        calls = []
        label_calls = []
        results = [
            {'task_id': 't', 'step': 'WAITING_CI',
             'codes': ['CI_WORKFLOW_JOBS_INVALID']},
            {'task_id': 't', 'step': 'WAITING_CI',
             'codes': ['CI_WORKFLOW_JOBS_INVALID']},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
            {'task_id': 't', 'step': 'MERGE_REFUSED',
             'codes': ['MERGE_MACOS_CHECK_REQUIRED']},
        ]

        def advance(**kwargs):
            calls.append(kwargs)
            return results.pop(0)

        def label_mac_ci(**kwargs):
            label_calls.append(kwargs)
            return {'step': 'WAITING_CI', 'codes': []}

        result = task_drive.drive(task_id='t', merge=True, max_steps=4,
                                  advance_call=advance, label_mac_ci_call=label_mac_ci,
                                  quota_read=self.quota_ok,
                                  sleep=lambda _seconds: None)

        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_MACOS_CHECK_REQUIRED'])
        self.assertEqual([call['merge'] for call in calls], [False, False, False, True])
        self.assertEqual(len(label_calls), 1)

    def test_skipped_mac_helper_retries_after_no_evidence_and_never_merges_early(self):
        calls = []
        label_calls = []
        label_mutations = []
        results = [
            {'task_id': 't', 'step': 'WAITING_CI',
             'codes': ['CI_WORKFLOW_JOBS_INVALID']},
            {'task_id': 't', 'step': 'WAITING_CI',
             'codes': ['CI_WORKFLOW_JOBS_INVALID']},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
            {'task_id': 't', 'step': 'MERGE_REFUSED',
             'codes': ['MERGE_MACOS_CHECK_REQUIRED']},
        ]

        def advance(**kwargs):
            calls.append(kwargs)
            return results.pop(0)

        def label_mac_ci(**kwargs):
            label_calls.append(kwargs)
            if len(label_calls) == 1:
                return None
            label_mutations.append('mac-ci')
            return {'step': 'WAITING_CI', 'codes': []}

        result = task_drive.drive(task_id='t', merge=True, max_steps=4,
                                  advance_call=advance, label_mac_ci_call=label_mac_ci,
                                  quota_read=self.quota_ok,
                                  sleep=lambda _seconds: None)

        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual([call['merge'] for call in calls], [False, False, False, True])
        self.assertEqual(len(label_calls), 2)
        self.assertEqual(label_mutations, ['mac-ci'])
        self.assertEqual(sum(call['merge'] for call in calls[:3]), 0)

    def test_real_skipped_mac_helper_rechecks_then_labels_before_ready_only(self):
        record = {'task_id': 't', 'completion': {'executor_task_id': 'executor'}}

        class Store:
            def get(self, _task_id):
                return record

        calls = []
        results = [
            {'task_id': 't', 'step': 'WAITING_CI',
             'codes': ['CI_WORKFLOW_JOBS_INVALID']},
            {'task_id': 't', 'step': 'WAITING_CI',
             'codes': ['CI_WORKFLOW_JOBS_INVALID']},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
            {'task_id': 't', 'step': 'MERGE_REFUSED',
             'codes': ['MERGE_MACOS_CHECK_REQUIRED']},
        ]

        def advance(**kwargs):
            calls.append(kwargs)
            return results.pop(0)

        pr_reads = 0
        label_mutations = []
        pr = {'number': 17, 'head': {'sha': 'a' * 40}, 'state': 'open',
              'merged': False, 'mergeable': True, 'labels': []}
        skipped_checks = {'total_count': 2, 'check_runs': [
            {'head_sha': 'a' * 40, 'name': 'check (Linux 3.14)',
             'status': 'completed', 'conclusion': 'success'},
            {'head_sha': 'a' * 40, 'name': 'check (macOS 3.13)',
             'status': 'completed', 'conclusion': 'skipped'},
        ]}

        def github_get(path):
            nonlocal pr_reads
            if path == 'pulls/17':
                pr_reads += 1
                current = dict(pr)
                current['head'] = dict(pr['head'])
                if pr_reads == 1:
                    current['mergeable'] = False
                return current
            if path.startswith('commits/'):
                return skipped_checks
            if path == 'pulls/17/files?per_page=100':
                return [{'filename': 'src/module.py'}]
            return []

        def runner(_root, args):
            label_mutations.append(args)
            return type('R', (), {'returncode': 0})()

        d6 = {'result_head': 'a' * 40, 'recorded_result_head': 'a' * 40,
              'dirty': False, 'state': 'CLEAN', 'root': '/synthetic/repo'}
        with patch.object(task_advance, '_d6_snapshot', return_value=d6):
            result = task_drive.drive(task_id='t', store=Store(), pr_number=17,
                merge=True, max_steps=4, advance_call=advance,
                github_get=github_get, merge_gh_runner=runner,
                quota_read=self.quota_ok, sleep=lambda _seconds: None)

        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual([call['merge'] for call in calls], [False, False, False, True])
        self.assertEqual(label_mutations,
                         [['pr', 'edit', '17', '--add-label', 'mac-ci']])

    def test_invalid_completion_evidence_without_skipped_mac_does_not_label(self):
        calls = []
        label_calls = []
        results = [
            {'task_id': 't', 'step': 'WAITING_CI',
             'codes': ['CI_WORKFLOW_JOBS_INVALID']},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
            {'task_id': 't', 'step': 'MERGED', 'codes': []},
        ]

        def advance(**kwargs):
            calls.append(kwargs)
            return results.pop(0)

        def label_mac_ci(**kwargs):
            label_calls.append(kwargs)
            return None

        result = task_drive.drive(task_id='t', merge=True, max_steps=3,
                                  advance_call=advance, label_mac_ci_call=label_mac_ci,
                                  quota_read=self.quota_ok,
                                  sleep=lambda _seconds: None)

        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual([call['merge'] for call in calls], [False, False, True])
        self.assertEqual(len(label_calls), 1)

    def test_labelled_mac_run_does_not_merge_while_completion_is_not_ready(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'WAITING_CI',
             'codes': ['CI_WORKFLOW_JOBS_INVALID']},
            {'task_id': 't', 'step': 'WAITING_CI',
             'codes': ['CI_WORKFLOW_JOBS_INVALID']},
        ])
        label_calls = []

        def label_mac_ci(**kwargs):
            label_calls.append(kwargs)
            return {'step': 'WAITING_CI', 'codes': []}

        result = task_drive.drive(task_id='t', merge=True, max_steps=2,
                                  advance_call=advance, label_mac_ci_call=label_mac_ci,
                                  quota_read=self.quota_ok,
                                  sleep=lambda _seconds: None)

        self.assertEqual(result['step'], 'MAX_STEPS')
        self.assertEqual([call['merge'] for call in calls], [False, False])
        self.assertEqual(len(label_calls), 1)

    def test_mac_wait_budget_returns_last_refusal_and_completed_failure_stops(self):
        pending_advance, pending_calls = self.adapter([
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': []},
            {'task_id': 't', 'step': 'MERGE_REFUSED',
             'codes': ['MERGE_MACOS_CHECK_REQUIRED']},
            {'task_id': 't', 'step': 'MERGE_REFUSED',
             'codes': ['MERGE_MACOS_CHECK_REQUIRED']},
        ])
        result = task_drive.drive(task_id='t', merge=True, max_steps=4,
                                  advance_call=pending_advance,
                                  quota_read=self.quota_ok,
                                  sleep=lambda _seconds: None)
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_MACOS_CHECK_REQUIRED'])
        self.assertEqual(len(pending_calls), 3)

        failed_advance, failed_calls = self.adapter([
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': []},
            {'task_id': 't', 'step': 'MERGE_REFUSED',
             'codes': ['MERGE_CHECKS_NOT_GREEN']},
        ])
        result = task_drive.drive(task_id='t', merge=True, max_steps=8,
                                  advance_call=failed_advance,
                                  quota_read=self.quota_ok,
                                  sleep=lambda _seconds: None)
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_CHECKS_NOT_GREEN'])
        self.assertEqual(len(failed_calls), 3)

    def test_merge_refusal_is_returned_without_polling(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
            {'task_id': 't', 'step': 'MERGE_REFUSED',
             'codes': ['MERGE_MACOS_CHECK_REQUIRED']},
        ])

        result = task_drive.drive(task_id='t', merge=True, advance_call=advance)

        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_MACOS_CHECK_REQUIRED'])
        self.assertEqual([call['merge'] for call in calls], [False, True])

    def test_waiting_ci_without_merge_does_not_attempt_merge(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']},
        ])

        result = task_drive.drive(task_id='t', merge=False, max_steps=1,
                                  advance_call=advance)

        self.assertEqual(result['step'], 'MAX_STEPS')
        self.assertEqual([call['merge'] for call in calls], [False])

    def test_trusted_terminal_ci_failure_stops_with_diagnostics(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'CI_FAILED',
             'codes': ['REQUIRED_CI_MISSING', 'REQUIRED_CI_FAILED']},
        ])

        result = task_drive.drive(task_id='t', merge=True, max_steps=8,
                                  advance_call=advance, sleep=lambda _seconds: None)

        self.assertEqual(result['step'], 'CI_FAILED')
        self.assertEqual(result['codes'], ['REQUIRED_CI_MISSING', 'REQUIRED_CI_FAILED'])
        self.assertEqual([call['merge'] for call in calls], [False])

        pending, _calls = self.adapter([
            {'task_id': 't', 'step': 'WAITING_CI',
             'codes': ['REQUIRED_CI_MISSING', 'REQUIRED_CI_PENDING']},
        ])
        result = task_drive.drive(task_id='t', max_steps=1,
                                  advance_call=pending, sleep=lambda _seconds: None)
        self.assertEqual(result['step'], 'MAX_STEPS')
        self.assertEqual(result['codes'], ['REQUIRED_CI_MISSING', 'REQUIRED_CI_PENDING'])

    def test_repeated_review_not_ready_stops_at_bounded_poll_attempts(self):
        pauses = []
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'RECORD_REVIEW_RESULT',
             'result': {'state': 'NOT_READY'}, 'codes': []},
            {'task_id': 't', 'step': 'RECORD_REVIEW_RESULT',
             'result': {'state': 'NOT_READY'}, 'codes': []},
        ])
        result = task_drive.drive(task_id='t', max_steps=2, poll_seconds=1,
                                  advance_call=advance, quota_read=self.quota_ok,
                                  sleep=pauses.append)
        self.assertEqual(result['step'], 'MAX_STEPS')
        self.assertEqual(result['steps'], 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(pauses, [1])

    def test_unfinished_initial_developer_stops_for_attention_without_polling(self):
        pauses = []
        advance, _calls = self.adapter([
            {'task_id': 't', 'step': 'DEVELOPER_ATTENTION',
             'codes': ['DEVELOPER_ATTENTION']}])
        result = task_drive.drive(task_id='t', poll_seconds=1,
                                  advance_call=advance, sleep=pauses.append)
        self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
        self.assertEqual(pauses, [])

    def test_rerun_after_crash_does_not_repeat_persisted_side_effect(self):
        state = {'index': 0, 'effects': []}
        results = [
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST', 'codes': []},
            {'task_id': 't', 'step': 'DISPATCH_REVIEW', 'codes': []},
            {'task_id': 't', 'step': 'CLOSED', 'codes': []},
        ]

        def advance(**_kwargs):
            # This models an existing durable claim/receipt. The effect is
            # persisted before the interrupted caller gets its result.
            result = results[state['index']]
            state['effects'].append(result['step'])
            state['index'] += 1
            if state['index'] == 1 and not state.get('crashed'):
                state['crashed'] = True
                raise RuntimeError('simulated process interruption')
            return result

        with self.assertRaises(RuntimeError):
            task_drive.drive(task_id='t', advance_call=advance)
        result = task_drive.drive(task_id='t', advance_call=advance)
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(state['effects'], [
            'RUN_REMOTE_TEST', 'DISPATCH_REVIEW', 'CLOSED'])

    def test_second_drive_on_finished_task_has_no_side_effect(self):
        state = {'finished': True, 'effects': 0, 'calls': 0}

        def advance(**_kwargs):
            state['calls'] += 1
            if not state['finished']:
                state['effects'] += 1
                state['finished'] = True
            return {'task_id': 't', 'step': 'CLOSED', 'codes': []}

        first = task_drive.drive(task_id='t', advance_call=advance)
        second = task_drive.drive(task_id='t', advance_call=advance)
        self.assertEqual(first['step'], 'CLOSED')
        self.assertEqual(second['step'], 'CLOSED')
        self.assertEqual(state['calls'], 2)
        self.assertEqual(state['effects'], 0)

    def test_drive_adapter_prints_one_machine_readable_result(self):
        # #1124: the adapter runs only for the Task's own PM session.
        native = '3f2b8c1e-4d5a-4b6c-8d7e-9f0a1b2c3d4e'
        args = parser_for_cli().parse_args(['task', 'drive', 'task-1',
                                            '--pm-session', native])
        owner_store = type('Store', (), {
            'get': lambda self, _task_id: {'pm_session_id': 'pm-' + native}})()
        output = []
        with patch.object(task_submission_cli, '_print', side_effect=output.append), \
                patch.dict('os.environ', {'CLAUDE_CODE_SESSION_ID': '',
                                          'CODEX_THREAD_ID': ''}):
            code = task_submission_cli.run_task_drive(
                args, store=owner_store, advance_call=lambda **_kwargs: {
                    'task_id': 'task-1', 'step': 'CLOSED', 'codes': []})
        self.assertEqual(code, 0)
        self.assertEqual(output, [{'task_id': 'task-1', 'step': 'CLOSED',
                                   'codes': [], 'steps': 1}])

    def test_without_merge_stops_at_integration_ready(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION',
             'codes': ['WAITING_INTEGRATION']}])
        result = task_drive.drive(task_id='t', advance_call=advance)
        self.assertEqual(result['step'], 'READY_FOR_INTEGRATION')
        self.assertEqual(result['codes'], ['WAITING_INTEGRATION'])
        self.assertEqual(len(calls), 1)

    def test_waits_for_ci_then_stops_on_unknown_and_owner_acceptance(self):
        pauses = []
        advance, _calls = self.adapter([
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']},
            {'task_id': 't', 'step': 'REVIEW_OUTCOME_UNKNOWN', 'codes': []},
        ])
        result = task_drive.drive(task_id='t', max_steps=4, poll_seconds=2,
                                  advance_call=advance, quota_read=self.quota_ok,
                                  sleep=pauses.append)
        self.assertEqual(result['step'], 'REVIEW_OUTCOME_UNKNOWN')
        self.assertEqual(pauses, [2])

        advance, _calls = self.adapter([
            {'task_id': 't', 'step': 'MERGE_REFUSED',
             'codes': ['MERGE_OWNER_ACCEPTANCE_MANUAL']}])
        result = task_drive.drive(task_id='t', merge=True, advance_call=advance)
        self.assertEqual(result['codes'], ['MERGE_OWNER_ACCEPTANCE_MANUAL'])

    def test_low_quota_stops_before_next_advance_with_waiting_github_quota(self):
        waiting_step = {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']}
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST', 'codes': []},
            waiting_step,
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
        ])
        quota_calls = []
        pauses = []

        def low_core():
            quota_calls.append(len(calls))
            return {
                'core': {'remaining': 299, 'reset': 1700000100},
                'graphql': {'remaining': 5000, 'reset': 1700000200},
            }

        result = task_drive.drive(task_id='t', poll_seconds=5,
                                  advance_call=advance, quota_read=low_core,
                                  sleep=pauses.append)
        self.assertEqual(result, {
            'task_id': 't',
            'step': 'WAITING_GITHUB_QUOTA',
            'result': {
                'resource': 'core',
                'remaining': 299,
                'reset': 1700000100,
                'last_result': waiting_step,
            },
            'codes': ['GITHUB_QUOTA_LOW'],
            'steps': 1,
        })
        self.assertEqual(len(calls), 2)
        self.assertEqual(quota_calls, [2])
        self.assertEqual(pauses, [])

        merge_waiting = {'task_id': 't', 'step': 'WAITING_CI', 'codes': []}
        merge_advance, merge_calls = self.adapter([
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
            merge_waiting,
            {'task_id': 't', 'step': 'MERGED', 'codes': []},
        ])
        result = task_drive.drive(
            task_id='t', merge=True, advance_call=merge_advance,
            quota_read=lambda: {
                'core': {'remaining': 300, 'reset': 1700000100},
                'graphql': {'remaining': 299, 'reset': 1700000200},
            },
            sleep=pauses.append)
        self.assertEqual(result, {
            'task_id': 't',
            'step': 'WAITING_GITHUB_QUOTA',
            'result': {
                'resource': 'graphql',
                'remaining': 299,
                'reset': 1700000200,
                'last_result': merge_waiting,
            },
            'codes': ['GITHUB_QUOTA_LOW'],
            'steps': 2,
        })
        self.assertEqual(len(merge_calls), 2)
        self.assertEqual(pauses, [])

    def test_unknown_quota_keeps_polling(self):
        pauses = []
        quota_calls = []
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']},
            {'task_id': 't', 'step': 'RECORD_REVIEW_RESULT',
             'result': {'state': 'NOT_READY'}, 'codes': []},
            {'task_id': 't', 'step': 'REFUSED', 'codes': ['GITHUB_EVIDENCE_UNAVAILABLE']},
        ])

        def unknown_quota():
            quota_calls.append(len(calls))
            return None

        result = task_drive.drive(task_id='t', poll_seconds=3,
                                  advance_call=advance, quota_read=unknown_quota,
                                  sleep=pauses.append)
        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(result['codes'], ['GITHUB_EVIDENCE_UNAVAILABLE'])
        self.assertEqual(len(calls), 3)
        self.assertEqual(quota_calls, [1, 2])
        self.assertEqual(pauses, [3, 6])

    def test_waiting_pauses_back_off_to_cap_and_reset_after_non_waiting_step(self):
        pauses = []
        advance, _calls = self.adapter([
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']},
            {'task_id': 't', 'step': 'RECORD_REVIEW_RESULT',
             'result': {'state': 'NOT_READY'}, 'codes': []},
            {'task_id': 't', 'step': 'WAITING_CI',
             'codes': ['CI_WORKFLOW_JOBS_INVALID']},
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']},
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']},
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST', 'codes': []},
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']},
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': []},
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': []},
            {'task_id': 't', 'step': 'MERGED', 'codes': []},
        ])
        result = task_drive.drive(
            task_id='t', merge=True, max_steps=20, poll_seconds=10,
            advance_call=advance,
            label_mac_ci_call=lambda **_kwargs: {'step': 'WAITING_CI', 'codes': []},
            quota_read=self.quota_ok, sleep=pauses.append)
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(pauses, [10, 20, 40, 60, 60, 10, 20, 10, 20])

        large_pauses = []
        large_advance, _calls = self.adapter([
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']},
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']},
            {'task_id': 't', 'step': 'CLOSED', 'codes': []},
        ])
        result = task_drive.drive(task_id='t', poll_seconds=75,
                                  advance_call=large_advance,
                                  quota_read=self.quota_ok,
                                  sleep=large_pauses.append)
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(large_pauses, [75, 75])

    def test_quota_read_is_not_called_when_no_pause_follows(self):
        quota_calls = []
        advance, _calls = self.adapter([
            {'task_id': 't', 'pr': 17, 'head': 'a' * 40, 'codes': []},
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST', 'codes': []},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
            {'task_id': 't', 'step': 'MERGED', 'codes': []},
        ])
        result = task_drive.drive(task_id='t', merge=True, advance_call=advance,
                                  quota_read=lambda: quota_calls.append(1))
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(quota_calls, [])

        waiting_advance, _calls = self.adapter([
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']},
        ])
        result = task_drive.drive(task_id='t', max_steps=1,
                                  advance_call=waiting_advance,
                                  quota_read=lambda: quota_calls.append(1))
        self.assertEqual(result['step'], 'MAX_STEPS')
        self.assertEqual(quota_calls, [])

    def test_stops_at_max_steps_and_finished_tasks_are_not_advanced_again(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST', 'codes': []},
            {'task_id': 't', 'step': 'CLOSED', 'codes': []},
        ])
        result = task_drive.drive(task_id='t', max_steps=1, advance_call=advance)
        self.assertEqual(result['step'], 'MAX_STEPS')
        self.assertEqual(len(calls), 1)

        advance, calls = self.adapter([{'task_id': 't', 'step': 'CLOSED', 'codes': []}])
        first = task_drive.drive(task_id='t', advance_call=advance)
        self.assertEqual(first['step'], 'CLOSED')
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
