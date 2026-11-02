import unittest

from llmtool import task_drive


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
