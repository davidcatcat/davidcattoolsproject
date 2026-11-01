import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool.task_brief import format_task_brief, task_brief
from llmtool.task_submission_cli import add_task_submission_parsers, run_task_brief
from llmtool.cli import main
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore


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
            'RUNNING': 1, 'BLOCKED': 0, 'UNAVAILABLE': 0, 'FAILED': 1,
            'COMPLETED': 0, 'ABANDONED': 0, 'UNKNOWN': 0, 'unreadable': 3})
        self.assertTrue(result['partial'])
        self.assertEqual([item['task_id'] for item in result['tasks']], ['a'])
        self.assertEqual(result['view'], {'scanned': 2, 'selected': 1, 'truncated': 1})

    def test_counts_each_status_and_unreadable(self):
        states = ('RUNNING', 'BLOCKED', 'UNAVAILABLE', 'FAILED', 'COMPLETED',
                  'ABANDONED', 'UNKNOWN')
        result = task_brief({'tasks': [row(str(index), state)
                                       for index, state in enumerate(states)],
                             'unreadable': 4})
        self.assertEqual({state: result['counts'][state] for state in states},
                         {state: 1 for state in states})
        self.assertEqual(result['counts']['unreadable'], 4)

    def test_missing_values_are_unknown_and_projection_omits_other_fields(self):
        result = task_brief({'tasks': [{
            'request': 'private request', 'response': 'private response',
            'prompt': 'secret prompt', 'path': '/private/path', 'log': 'private log',
            'review_claim_state': 'REVIEW_PASS',
        }], 'unreadable': 0})
        self.assertEqual(result['tasks'], [{
            'task_id': 'UNKNOWN', 'state': 'UNKNOWN',
            'state_label': 'derived from persisted Task status',
            'last_event': {'state': 'UNKNOWN'},
            'review_claim_state': 'REVIEW_PASS',
            'review_claim_label': 'stored claim state; not a formal verdict',
            'pending_inbox_count': 'UNKNOWN',
        }])
        rendered = format_task_brief(result, as_json=True)
        for secret in ('private request', 'private response', 'secret prompt',
                       '/private/path', 'private log'):
            self.assertNotIn(secret, rendered)
        self.assertIn('REVIEW_PASS', rendered)
        self.assertIn('not a formal verdict', rendered)

    def test_summary_fields_are_projected_and_claim_is_labeled(self):
        result = task_brief({'tasks': [row(
            't1', 'COMPLETED', last_event={'state': 'CLOSED'},
            review_claim_state='REVIEW_PASS', pending_inbox_count=2)], 'unreadable': 0})
        item = result['tasks'][0]
        self.assertEqual(item['last_event']['state'], 'CLOSED')
        self.assertEqual(item['review_claim_state'], 'REVIEW_PASS')
        self.assertIn('not a formal verdict', item['review_claim_label'])
        self.assertEqual(item['pending_inbox_count'], 2)

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

    def test_task_brief_top_level_cli_routing(self):
        output = io.StringIO()
        rows = [row('done', 'COMPLETED'), row('blocked', 'BLOCKED')]
        with patch('llmtool.task_status.task_status', return_value={
                'tasks': rows, 'unreadable': 0}) as status:
            with patch('sys.stdout', output):
                self.assertEqual(main(['task', 'brief', '--limit', '1', '--json']), 0)
        status.assert_called_once_with(pm_session=None, limit=200, store=None)
        result = json.loads(output.getvalue())
        self.assertTrue(result['partial'])
        self.assertEqual(result['tasks'][0]['task_id'], 'blocked')

    def test_cli_rejects_invalid_limit_before_status_call(self):
        parser = argparse.ArgumentParser()
        root = parser.add_subparsers(dest='command', required=True)
        task = root.add_parser('task')
        actions = task.add_subparsers(dest='action', required=True)
        add_task_submission_parsers(actions)
        for limit in ('0', '201'):
            args = parser.parse_args(['task', 'brief', '--limit', limit])
            with self.subTest(limit=limit), patch(
                    'llmtool.task_status.task_status') as status:
                with self.assertRaises(LLMToolError):
                    run_task_brief(args, stdout=io.StringIO())
                status.assert_not_called()

    def test_cli_rejects_invalid_pm_session(self):
        parser = argparse.ArgumentParser()
        root = parser.add_subparsers(dest='command', required=True)
        task = root.add_parser('task')
        actions = task.add_subparsers(dest='action', required=True)
        add_task_submission_parsers(actions)
        args = parser.parse_args(['task', 'brief', '--pm-session', 'invalid'])
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(Path(root) / 'tasks')
            with self.assertRaises(LLMToolError):
                run_task_brief(args, store=store, stdout=io.StringIO())

    def test_corrupt_record_is_reported_without_changing_records_or_inbox(self):
        with tempfile.TemporaryDirectory() as root:
            store = TaskStore(Path(root) / 'tasks')
            task_id = 'a' * 32
            store.root.mkdir(mode=0o700, parents=True)
            record_path = store.root / f'{task_id}.json'
            record_path.write_text('{broken', encoding='utf-8')
            inbox_path = store.root / 'inbox'
            inbox_path.mkdir()
            inbox_file = inbox_path / 'pending.json'
            inbox_file.write_text('{"pending":true}', encoding='utf-8')
            before_record = record_path.read_bytes()
            before_inbox = inbox_file.read_bytes()
            parser = argparse.ArgumentParser()
            root_parser = parser.add_subparsers(dest='command', required=True)
            task = root_parser.add_parser('task')
            actions = task.add_subparsers(dest='action', required=True)
            add_task_submission_parsers(actions)
            args = parser.parse_args(['task', 'brief', '--json'])
            output = io.StringIO()
            self.assertEqual(run_task_brief(args, store=store, stdout=output), 0)
            self.assertEqual(json.loads(output.getvalue())['counts']['unreadable'], 1)
            self.assertEqual(record_path.read_bytes(), before_record)
            self.assertEqual(inbox_file.read_bytes(), before_inbox)


if __name__ == '__main__':
    unittest.main()
