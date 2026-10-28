import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import task_status
from llmtool.cli import main, parser_for_cli
from llmtool.task_store import TaskStore
from test_task_store import record as valid_record


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


class CountingTaskStore(TaskStore):
    def __init__(self, root):
        super().__init__(root)
        self.get_calls = 0

    def get(self, task_id):
        self.get_calls += 1
        return super().get(task_id)


def record(root, **extra):
    """Start status fixtures with a record accepted and persisted by TaskStore."""
    store = TaskStore(Path(root).resolve() / 'tasks')
    value = valid_record()
    value.update(extra)
    return store.create(value)


class TaskStatusTests(unittest.TestCase):
    def test_derived_states(self):
        with tempfile.TemporaryDirectory() as root:
            base = record(root)
        mappings = (
            ('task', {
                'PM_PENDING': 'RUNNING', 'PM_RESPONDED': 'RUNNING',
                'PM_UNAVAILABLE': 'UNAVAILABLE', 'INPUT_REJECTED': 'FAILED',
                'PM_RESPONSE_REJECTED': 'FAILED',
            }),
            ('developer', {
                'DEVELOPER_PENDING': 'RUNNING', 'DEVELOPER_DISPATCHING': 'RUNNING',
                'DEVELOPER_RESPONDED': 'RUNNING', 'DEVELOPER_UNAVAILABLE': 'UNAVAILABLE',
                'DEVELOPER_OUTCOME_UNKNOWN': 'UNKNOWN',
                'DEVELOPER_OUTPUT_REJECTED': 'FAILED',
            }),
            ('callback', {
                'WAITING_EXECUTOR': 'RUNNING', 'VERIFYING_EXECUTOR': 'RUNNING',
                'WAITING_PM': 'RUNNING', 'PM_EVIDENCE_READY': 'RUNNING',
                'PM_DISPATCHING': 'RUNNING', 'PM_RETURNED': 'COMPLETED',
                'PM_RESPONSE_REJECTED': 'FAILED', 'PM_UNAVAILABLE': 'UNAVAILABLE',
                'PM_OUTCOME_UNKNOWN': 'UNKNOWN',
            }),
            ('completion', {
                'WAITING_EXECUTOR': 'RUNNING', 'WAITING_REVIEW': 'RUNNING',
                'WAITING_CI': 'RUNNING', 'WAITING_PM': 'RUNNING',
                'PM_EVIDENCE_READY': 'RUNNING', 'PM_DISPATCHING': 'RUNNING',
                'PM_RETURNED': 'COMPLETED', 'PM_RESPONSE_REJECTED': 'FAILED',
                'PM_UNAVAILABLE': 'UNAVAILABLE', 'PM_OUTCOME_UNKNOWN': 'UNKNOWN',
            }),
        )
        self.assertEqual(set(mappings[0][1]), set(task_status.STATES))
        for kind, states in mappings:
            for state, expected in states.items():
                if kind == 'task':
                    value = {**base, 'state': state}
                elif kind == 'developer':
                    value = {**base, 'developer': {'state': state}}
                elif kind == 'callback':
                    value = {**base, 'developer': {'direct_result_callback': {
                        'state': state}}}
                else:
                    value = {**base, 'completion': {'state': state}}
                with self.subTest(kind=kind, state=state):
                    self.assertEqual(task_status.derive_state(value), expected)
        blocked = {**base, 'developer': {
            'state': 'DEVELOPER_OUTCOME_UNKNOWN', 'error_code': 'CODEX_BLOCKED'}}
        self.assertEqual(task_status.derive_state(blocked), 'BLOCKED')

    def test_unknown_shape_is_unknown(self):
        self.assertEqual(task_status.derive_state({'state': 'NEW_STATE'}), 'UNKNOWN')
        self.assertEqual(task_status.derive_state({
            'state': 'PM_PENDING', 'developer': {'state': 'FUTURE_DEVELOPER_STATE'}}),
            'UNKNOWN')
        self.assertEqual(task_status.derive_state({
            'state': 'PM_PENDING', 'developer': {'direct_result_callback': {
                'state': 'FUTURE_CALLBACK_STATE'}}}), 'UNKNOWN')
        self.assertEqual(task_status.derive_state({
            'state': 'PM_PENDING', 'completion': {'state': 'FUTURE_COMPLETION_STATE'}}),
            'UNKNOWN')

    def test_merged_github_event_completes_task(self):
        with tempfile.TemporaryDirectory() as root:
            base = record(root)
        base['completion_events'] = [{
            'source': 'GITHUB', 'marker_type': 'merge', 'marker_status': 'MERGED'}]
        self.assertEqual(task_status.derive_state(base), 'COMPLETED')

    def test_responded_developer_uses_completion_state(self):
        with tempfile.TemporaryDirectory() as root:
            base = record(root)
        outcomes = {
            'PM_RETURNED': 'COMPLETED',
            'PM_RESPONSE_REJECTED': 'FAILED',
            'PM_UNAVAILABLE': 'UNAVAILABLE',
            'PM_OUTCOME_UNKNOWN': 'UNKNOWN',
            'WAITING_EXECUTOR': 'RUNNING',
            'WAITING_REVIEW': 'RUNNING',
            'WAITING_CI': 'RUNNING',
            'WAITING_PM': 'RUNNING',
            'PM_EVIDENCE_READY': 'RUNNING',
            'PM_DISPATCHING': 'RUNNING',
        }
        for completion_state, expected in outcomes.items():
            value = {
                **base,
                'developer': {'state': 'DEVELOPER_RESPONDED'},
                'completion': {'state': completion_state},
            }
            with self.subTest(completion=completion_state):
                self.assertEqual(task_status.derive_state(value), expected)

    def test_terminal_developer_state_precedes_completion(self):
        with tempfile.TemporaryDirectory() as root:
            base = record(root)
        cases = (
            ({'state': 'DEVELOPER_UNAVAILABLE'}, 'UNAVAILABLE'),
            ({'state': 'DEVELOPER_OUTPUT_REJECTED'}, 'FAILED'),
            ({'state': 'DEVELOPER_OUTCOME_UNKNOWN'}, 'UNKNOWN'),
            ({'state': 'DEVELOPER_OUTCOME_UNKNOWN', 'error_code': 'CODEX_BLOCKED'}, 'BLOCKED'),
        )
        for developer, expected in cases:
            value = {
                **base,
                'developer': developer,
                'completion': {'state': 'PM_RETURNED'},
            }
            with self.subTest(developer=developer):
                self.assertEqual(task_status.derive_state(value), expected)

    def test_temp_store_limit_filter_unreadable_and_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            rows = [record(root, pm_session_id='pm-one' if i != 2 else 'pm-two')
                    for i in range(1, 4)]
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

    def test_index_matches_full_scan_and_skips_unprinted_reads(self):
        with tempfile.TemporaryDirectory() as root:
            rows = [record(root, created_at=f'2026-10-2{i}T00:00:00Z',
                           pm_session_id='pm-one' if i < 3 else 'pm-two')
                    for i in range(1, 5)]
            store = CountingTaskStore(Path(root) / 'tasks')
            unreadable_id = 'f' * 32
            (store.root / f'{unreadable_id}.json').write_text('invalid')

            full = task_status.task_status(store=store, limit=2)
            self.assertEqual(full['unreadable'], 1)
            store.get_calls = 0
            indexed = task_status.task_status(store=store, limit=2)
            self.assertEqual(indexed, full)
            self.assertEqual(store.get_calls, 2)

            filtered = task_status.task_status(store=store, pm_session='pm-two')
            index_path = Path(root) / 'task-status-index.json'
            index_path.unlink()
            without_index = task_status.task_status(store=store, pm_session='pm-two')
            self.assertEqual(without_index, filtered)
            self.assertEqual(len(rows), 4)

    def test_changed_unprinted_record_is_revalidated(self):
        with tempfile.TemporaryDirectory() as root:
            rows = [record(root, created_at=f'2026-10-2{i}T00:00:00Z')
                    for i in range(1, 4)]
            store = CountingTaskStore(Path(root) / 'tasks')
            task_status.task_status(store=store, limit=1)
            old_record = min(rows, key=lambda item: item['created_at'])
            os.utime(store.root / f"{old_record['task_id']}.json", None)
            store.get_calls = 0
            task_status.task_status(store=store, limit=1)
            self.assertEqual(store.get_calls, 2)

    def test_invalid_index_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            record(root, created_at='2026-10-29T00:00:00Z')
            store = CountingTaskStore(Path(root) / 'tasks')
            expected = task_status.task_status(store=store)
            index_path = Path(root) / 'task-status-index.json'

            index_path.write_text('{')
            self.assertEqual(task_status.task_status(store=store), expected)
            index_path.write_text('{}')
            index_path.chmod(0o644)
            self.assertEqual(task_status.task_status(store=store), expected)
            index_path.unlink()
            target = Path(root) / 'index-target'
            target.write_text('{}')
            index_path.symlink_to(target)
            self.assertEqual(task_status.task_status(store=store), expected)

    def test_validator_fingerprint_change_invalidates_index(self):
        with tempfile.TemporaryDirectory() as root:
            record(root, created_at='2026-10-29T00:00:00Z')
            store = CountingTaskStore(Path(root) / 'tasks')
            task_status.task_status(store=store)
            store.get_calls = 0
            with patch('llmtool.task_index.validator_fingerprint', return_value='f' * 64):
                task_status.task_status(store=store)
            self.assertGreaterEqual(store.get_calls, 1)


if __name__ == '__main__':
    unittest.main()
