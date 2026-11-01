import argparse
import io
import json
import unittest
from unittest.mock import patch

from llmtool.task_brief import format_task_brief, task_brief
from llmtool.task_submission_cli import add_task_submission_parsers, run_task_brief


def row(task_id, state, **extra):
    return {'task_id': task_id, 'state': state, **extra}


class TaskBriefTests(unittest.TestCase):
    def test_attention_priority_then_running_then_terminal_and_id(self):
        result = task_brief({'tasks': [
            row('z', 'COMPLETED'), row('d', 'UNKNOWN'), row('c', 'UNAVAILABLE'),
            row('b', 'FAILED'), row('a', 'BLOCKED'), row('x', 'RUNNING'),
            row('y', 'ABANDONED'),
        ], 'unreadable': 0})
        self.assertEqual([item['state'] for item in result['tasks']], [
            'BLOCKED', 'FAILED', 'UNAVAILABLE', 'UNKNOWN', 'RUNNING', 'COMPLETED', 'ABANDONED'])
        self.assertEqual([item['task_id'] for item in result['tasks'][:4]],
                         ['a', 'b', 'c', 'd'])

    def test_counts_unreadable_and_partial_limit(self):
        result = task_brief({'tasks': [row('b', 'RUNNING'), row('a', 'FAILED')],
                             'unreadable': 3}, limit=1)
        self.assertEqual(result['counts'], {
            'attention': 1, 'running': 1, 'terminal': 0, 'unreadable': 3})
        self.assertTrue(result['partial'])
        self.assertEqual([item['task_id'] for item in result['tasks']], ['a'])

    def test_missing_values_are_unknown_and_projection_omits_other_fields(self):
        result = task_brief({'tasks': [{
            'request': 'private request', 'response': 'private response',
            'prompt': 'secret prompt', 'path': '/private/path', 'log': 'private log',
            'review_claim_state': 'REVIEW_PASS',
        }], 'unreadable': 0})
        self.assertEqual(result['tasks'], [{
            'task_id': 'UNKNOWN', 'state': 'UNKNOWN',
            'state_label': 'derived from persisted Task status',
        }])
        rendered = format_task_brief(result, as_json=True)
        for secret in ('private request', 'private response', 'secret prompt',
                       '/private/path', 'private log', 'REVIEW_PASS'):
            self.assertNotIn(secret, rendered)

    def test_empty_human_output(self):
        result = task_brief({'tasks': [], 'unreadable': 0})
        self.assertEqual(format_task_brief(result), '沒有可顯示的任務')

    def test_json_schema_and_provenance(self):
        result = task_brief({'tasks': [row('t1', 'COMPLETED')], 'unreadable': 0})
        encoded = format_task_brief(result, as_json=True)
        self.assertEqual(json.loads(encoded)['schema_version'], 1)
        self.assertEqual(json.loads(encoded)['provenance']['source'], 'task_status')

    def test_limit_validation(self):
        for limit in (0, 201, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                task_brief({'tasks': []}, limit=limit)

    def test_task_brief_cli_parser_and_handler(self):
        parser = argparse.ArgumentParser()
        root = parser.add_subparsers(dest='command', required=True)
        task = root.add_parser('task')
        actions = task.add_subparsers(dest='action', required=True)
        add_task_submission_parsers(actions)
        args = parser.parse_args(['task', 'brief', '--limit', '1', '--pm-session',
                                  'pm-test', '--json'])
        output = io.StringIO()
        with patch('llmtool.task_status.task_status', return_value={
                'tasks': [row('t1', 'RUNNING')], 'unreadable': 0}) as status:
            self.assertEqual(run_task_brief(args, stdout=output), 0)
        status.assert_called_once_with(pm_session='pm-test', limit=200, store=None)
        self.assertEqual(json.loads(output.getvalue())['tasks'][0]['state'], 'RUNNING')


if __name__ == '__main__':
    unittest.main()
