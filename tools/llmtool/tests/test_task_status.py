import hashlib
import io
import tempfile
import unittest
from unittest.mock import patch

from llmtool import task_status
from llmtool.cli import main, parser_for_cli
from llmtool.task_store import TaskStore


class MemoryTaskStore(TaskStore):
    def __init__(self, root, records, unreadable=()):
        super().__init__(root)
        self.records = records
        self.unreadable = set(unreadable)

    def task_ids(self):
        return [record['task_id'] for record in self.records] + list(self.unreadable)

    def get(self, task_id):
        if task_id in self.unreadable:
            raise ValueError('bad record')
        return next(dict(record) for record in self.records if record['task_id'] == task_id)


def record(task_id, session, created, state='PM_PENDING', **extra):
    return {'task_id': task_id, 'pm_session_id': session, 'created_at': created,
            'state': state, 'record_sha256': 'a' * 64, **extra}


class TaskStatusTests(unittest.TestCase):
    def test_derived_states(self):
        cases = [
            (record('a', 'pm-one', '2026-01-01T00:00:00Z'), 'RUNNING'),
            (record('a', 'pm-one', '2026-01-01T00:00:00Z', 'PM_RESPONDED'), 'RUNNING'),
            (record('a', 'pm-one', '2026-01-01T00:00:00Z', 'PM_UNAVAILABLE'), 'UNAVAILABLE'),
            (record('a', 'pm-one', '2026-01-01T00:00:00Z', 'INPUT_REJECTED'), 'FAILED'),
            (record('a', 'pm-one', '2026-01-01T00:00:00Z', 'PM_PENDING', completion={'state': 'BLOCKED'}), 'BLOCKED'),
            (record('a', 'pm-one', '2026-01-01T00:00:00Z', 'PM_PENDING', completion={'state': 'UNKNOWN'}), 'UNKNOWN'),
            (record('a', 'pm-one', '2026-01-01T00:00:00Z', 'PM_PENDING', developer={'state': 'DEVELOPER_RESPONDED'}), 'RUNNING'),
            (record('a', 'pm-one', '2026-01-01T00:00:00Z', 'PM_PENDING', developer={'state': 'DEVELOPER_BLOCKED'}), 'BLOCKED'),
            (record('a', 'pm-one', '2026-01-01T00:00:00Z', 'PM_PENDING', developer={'direct_result_callback': {'state': 'PM_OUTCOME_UNKNOWN'}}), 'UNKNOWN'),
        ]
        for value, expected in cases:
            with self.subTest(expected=expected, record=value):
                self.assertEqual(task_status.derive_state(value), expected)

    def test_unknown_shape_is_unknown(self):
        self.assertEqual(task_status.derive_state({'state': 'NEW_STATE'}), 'UNKNOWN')

    def test_temp_store_limit_filter_unreadable_and_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            rows = [record(f'{i:032x}', 'pm-one' if i != 2 else 'pm-two',
                           f'2026-01-0{i + 1}T00:00:00Z') for i in range(1, 4)]
            before = [hashlib.sha256(row['record_sha256'].encode()).hexdigest() for row in rows]
            store = MemoryTaskStore(root, rows, unreadable=('f' * 32,))
            result = task_status.task_status(store=store, limit=1)
            self.assertEqual(len(result['tasks']), 1)
            self.assertEqual(result['unreadable'], 1)
            filtered = task_status.task_status(store=store, pm_session='pm-two')
            self.assertEqual([row['pm_session_id'] for row in filtered['tasks']], ['pm-two'])
            self.assertEqual(before, [hashlib.sha256(row['record_sha256'].encode()).hexdigest() for row in rows])

    def test_limit_bound(self):
        with self.assertRaises(ValueError):
            task_status.task_status(limit=201, store=MemoryTaskStore('/tmp/unused', []))

    def test_cli_route_reaches_handler(self):
        args = parser_for_cli().parse_args(['task', 'status', '--pm-session', 'pm-one', '--limit', '3', '--json'])
        self.assertEqual(args.action, 'status')
        output = io.StringIO()
        with patch('llmtool.task_status.TaskStore') as store_class:
            store_class.return_value.task_ids.return_value = []
            with patch('sys.stdout', output):
                self.assertEqual(main(['task', 'status', '--json']), 0)
        self.assertIn('"tasks":[]', output.getvalue())


if __name__ == '__main__':
    unittest.main()
