import unittest
from unittest.mock import patch

from llmtool import task_drive
from llmtool import task_submission_cli
from llmtool.cli import parser_for_cli


class TaskDriveTests(unittest.TestCase):
    def adapter(self, results):
        calls = []

        def advance(**kwargs):
            calls.append(kwargs)
            return results.pop(0)

        return advance, calls

    def test_drives_each_step_and_merges_only_when_requested(self):
        steps = [
            {'task_id': 't', 'pr': 17, 'head': 'a' * 40, 'codes': []},
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST',
             'result': {'result': 'PASS'}, 'codes': []},
            {'task_id': 't', 'step': 'DISPATCH_REVIEW', 'codes': []},
            {'task_id': 't', 'step': 'RECORD_REVIEW_RESULT',
             'result': {'outcome': 'REVIEW_PASS'}, 'codes': []},
            {'task_id': 't', 'step': 'RUN_TASK_COMPLETE', 'codes': []},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
            {'task_id': 't', 'step': 'MERGED', 'codes': []},
        ]
        advance, calls = self.adapter(steps)
        result = task_drive.drive(task_id='t', merge=True, max_steps=7,
                                  advance_call=advance)
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual([call['merge'] for call in calls], [False] * 6 + [True])

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
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
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
                                  advance_call=advance, sleep=pauses.append)
        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(pauses, [1, 1])

    def test_pending_review_not_ready_waits_without_counting_a_workflow_step(self):
        pauses = []
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'RECORD_REVIEW_RESULT',
             'result': {'state': 'NOT_READY'}, 'codes': []},
            {'task_id': 't', 'step': 'RECORD_REVIEW_RESULT',
             'result': {'outcome': 'REVIEW_PASS'}, 'codes': []},
            {'task_id': 't', 'step': 'RUN_TASK_COMPLETE', 'codes': []},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
        ])
        result = task_drive.drive(task_id='t', poll_seconds=0.25,
                                  advance_call=advance, sleep=pauses.append)
        self.assertEqual(result['step'], 'READY_FOR_INTEGRATION')
        self.assertEqual(result['steps'], 3)
        self.assertEqual(len(calls), 4)
        self.assertEqual(pauses, [0.25])

    def test_pending_integration_waits_on_advance_result_code(self):
        pauses = []
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION',
             'codes': ['WAITING_INTEGRATION']},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
        ])
        result = task_drive.drive(task_id='t', poll_seconds=0.5,
                                  advance_call=advance, sleep=pauses.append)
        self.assertEqual(result['step'], 'READY_FOR_INTEGRATION')
        self.assertEqual(result['steps'], 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(pauses, [0.5])

    def test_repeated_review_not_ready_stops_at_bounded_poll_attempts(self):
        pauses = []
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'RECORD_REVIEW_RESULT',
             'result': {'state': 'NOT_READY'}, 'codes': []},
            {'task_id': 't', 'step': 'RECORD_REVIEW_RESULT',
             'result': {'state': 'NOT_READY'}, 'codes': []},
        ])
        result = task_drive.drive(task_id='t', max_steps=2, poll_seconds=1,
                                  advance_call=advance, sleep=pauses.append)
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
        args = parser_for_cli().parse_args(['task', 'drive', 'task-1'])
        output = []
        with patch.object(task_submission_cli, '_print', side_effect=output.append):
            code = task_submission_cli.run_task_drive(
                args, store=object(), advance_call=lambda **_kwargs: {
                    'task_id': 'task-1', 'step': 'CLOSED', 'codes': []})
        self.assertEqual(code, 0)
        self.assertEqual(output, [{'task_id': 'task-1', 'step': 'CLOSED',
                                   'codes': [], 'steps': 1}])

    def test_without_merge_stops_at_integration_ready(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []}])
        result = task_drive.drive(task_id='t', advance_call=advance)
        self.assertEqual(result['step'], 'READY_FOR_INTEGRATION')
        self.assertEqual(len(calls), 1)

    def test_waits_for_ci_then_stops_on_unknown_and_owner_acceptance(self):
        pauses = []
        advance, _calls = self.adapter([
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['WAITING_CI']},
            {'task_id': 't', 'step': 'REVIEW_OUTCOME_UNKNOWN', 'codes': []},
        ])
        result = task_drive.drive(task_id='t', max_steps=4, poll_seconds=2,
                                  advance_call=advance, sleep=pauses.append)
        self.assertEqual(result['step'], 'REVIEW_OUTCOME_UNKNOWN')
        self.assertEqual(pauses, [2])

        advance, _calls = self.adapter([
            {'task_id': 't', 'step': 'MERGE_REFUSED',
             'codes': ['MERGE_OWNER_ACCEPTANCE_MANUAL']}])
        result = task_drive.drive(task_id='t', merge=True, advance_call=advance)
        self.assertEqual(result['codes'], ['MERGE_OWNER_ACCEPTANCE_MANUAL'])

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
