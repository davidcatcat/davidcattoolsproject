import json
import unittest

from llmtool import review_repair
from llmtool.errors import LLMToolError


class ReviewRepairAcceptanceFullTests(unittest.TestCase):
    def setUp(self):
        self.task = {
            'task_id': 'task-synthetic-1',
            'pm_provenance': {
                'pm_native_session_id': 'session-synthetic-1',
                'pm_cwd': '/workspace/repo',
            },
            'pm_assignment': {
                'goal': 'Synthetic repair goal for testing.',
            },
        }
        self.packet = {
            'version': 1,
            'verdict_comment_id': 9,
            'failed_head': '0123456789abcdef0123456789abcdef01234567',
            'findings_count': 1,
        }
        self.recorded_streams = []

    def stub_record_pm(self, args, store=None, task_id=None, input_stream=None, return_record=True):
        text = input_stream.read() if input_stream is not None else ''
        self.recorded_streams.append(text)
        return {'task_id': 'repair-task-1'}

    def test_sixteen_original_items_refuse_acceptance_full(self):
        developer = {
            'provider_profile': 'agy',
            'acceptance': [f'criterion {i}' for i in range(16)],
        }
        with self.assertRaises(LLMToolError) as ctx:
            review_repair._record_repair_task(
                task=self.task,
                developer=developer,
                packet=self.packet,
                attempt=1,
                store=None,
                record_pm=self.stub_record_pm,
            )
        self.assertEqual(ctx.exception.review_repair_code, 'REPAIR_ACCEPTANCE_FULL')
        self.assertEqual(len(self.recorded_streams), 0)

    def test_fifteen_original_items_succeeds_and_records_acceptance(self):
        developer = {
            'provider_profile': 'agy',
            'acceptance': [f'criterion {i}' for i in range(15)],
        }
        record = review_repair._record_repair_task(
            task=self.task,
            developer=developer,
            packet=self.packet,
            attempt=1,
            store=None,
            record_pm=self.stub_record_pm,
        )
        self.assertEqual(len(self.recorded_streams), 1)
        self.assertEqual(record, {'task_id': 'repair-task-1'})

        payload = json.loads(self.recorded_streams[0])
        self.assertIn('request', payload)
        self.assertIn('response', payload)

        response = json.loads(payload['response'])
        assignment = response.get('developer_assignment', {})
        acceptance = assignment.get('acceptance', [])
        self.assertEqual(len(acceptance), 16)
        self.assertTrue(acceptance[-1].startswith('Repair attempt 1/'))

    def test_non_list_developer_acceptance_refuses_binding_invalid(self):
        developer = {
            'provider_profile': 'agy',
            'acceptance': 'not-a-valid-list',
        }
        with self.assertRaises(LLMToolError) as ctx:
            review_repair._record_repair_task(
                task=self.task,
                developer=developer,
                packet=self.packet,
                attempt=1,
                store=None,
                record_pm=self.stub_record_pm,
            )
        self.assertEqual(ctx.exception.review_repair_code, 'REPAIR_DEVELOPER_BINDING_INVALID')
        self.assertEqual(len(self.recorded_streams), 0)

    def test_missing_pm_assignment_goal_refuses_binding_invalid(self):
        task_without_goal = {
            'task_id': 'task-synthetic-1',
            'pm_provenance': {
                'pm_native_session_id': 'session-synthetic-1',
                'pm_cwd': '/workspace/repo',
            },
            'pm_assignment': {},
        }
        developer = {
            'provider_profile': 'agy',
            'acceptance': ['criterion 0'],
        }
        with self.assertRaises(LLMToolError) as ctx:
            review_repair._record_repair_task(
                task=task_without_goal,
                developer=developer,
                packet=self.packet,
                attempt=1,
                store=None,
                record_pm=self.stub_record_pm,
            )
        self.assertEqual(ctx.exception.review_repair_code, 'REPAIR_DEVELOPER_BINDING_INVALID')
        self.assertEqual(len(self.recorded_streams), 0)


if __name__ == '__main__':
    unittest.main()
