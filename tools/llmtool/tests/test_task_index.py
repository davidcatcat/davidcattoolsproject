"""#1838: the task status index keeps an optional TUI projection of each readable record."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import chat_tui_status, task_index


def _record(task_id, **fields):
    """A synthetic TaskStore-shaped record (no real Task data, no credential-shaped values)."""
    base = {'task_id': task_id, 'state': 'PM_RESPONDED', 'developer': None,
            'request': 'synthetic request\nsecond line', 'created_at': '2026-11-14T00:00:00Z',
            'updated_at': '2026-11-14T01:00:00Z', 'pm_session_id': None}
    base.update(fields)
    return base


class _Store:
    def __init__(self, root, records):
        self.root = root
        self.records = records

    def get(self, task_id):
        return dict(self.records[task_id])

    def task_ids(self):
        return sorted(self.records)


class TaskIndexTuiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name).resolve() / 'state'
        self.root = self.state / 'tasks'
        self.root.mkdir(parents=True)
        os.chmod(self.state, 0o700)
        os.chmod(self.root, 0o700)
        self.task_id = 'a' * 32
        self.store = _Store(self.root, {self.task_id: _record(self.task_id)})
        (self.root / (self.task_id + '.json')).write_text('{}')
        self.path = self.state / 'task-status-index.json'

    def saved(self):
        index = task_index.TaskStatusIndex(self.store)
        _record_read, readable = index.remember_full_scan(self.task_id)
        self.assertTrue(readable)
        index.flush()
        return json.loads(self.path.read_text())['entries'][self.task_id]

    def test_written_entry_carries_the_tui_projection_and_reads_back(self):
        entry = self.saved()
        self.assertEqual(entry['tui'], chat_tui_status.index_summary(_record(self.task_id)))
        self.assertEqual(entry['tui']['name'], 'synthetic request')
        self.assertTrue(task_index._valid_tui(entry['tui']))
        self.assertEqual(task_index.TaskStatusIndex(self.store).entries[self.task_id], entry)
        entries, reason = task_index.display_entries(self.store)
        self.assertIsNone(reason)
        self.assertEqual(entries[self.task_id], entry)
        self.assertEqual(entry['stat'], task_index.record_key(self.store, self.task_id))

    def test_projection_failure_keeps_the_status_entry_without_tui(self):
        with patch.object(chat_tui_status, 'index_summary', side_effect=ValueError('shape')):
            entry = self.saved()
        self.assertNotIn('tui', entry)
        self.assertEqual(entry['created_at'], '2026-11-14T00:00:00Z')
        self.assertEqual(task_index.TaskStatusIndex(self.store).entries[self.task_id], entry)

    def test_oversize_payload_drops_only_the_tui_projection(self):
        tui = self.saved()['tui']
        full = self.path.stat().st_size
        projection = len(json.dumps(tui, sort_keys=True, separators=(',', ':')))
        os.unlink(self.path)
        # A bound the full payload exceeds but the payload without the projection fits.
        with patch.object(task_index, '_MAX_INDEX_BYTES', full - projection // 2):
            entry = self.saved()
        self.assertNotIn('tui', entry)
        self.assertEqual(entry['readable'], True)

    def test_invalid_tui_rejects_the_index(self):
        entry = self.saved()
        for bad in ({'stage': 7}, {'events': [['t', 'TASK', 'x', True]] * 4},
                    {'event_count': 0, 'events': [[None, 'TASK', 'x', True]]},
                    {'events': [[None, 'TASK', 'x' * (task_index.TUI_TEXT_LIMIT + 1), True]]},
                    {'prs': [True]}, {'extra': 1}):
            with self.subTest(bad):
                broken = dict(entry, tui=dict(entry['tui'], **bad))
                self.assertFalse(task_index._valid_entry(self.task_id, broken))
                data = json.loads(self.path.read_text())
                data['entries'][self.task_id] = broken
                self.path.write_text(json.dumps(data))
                self.assertEqual(task_index.display_entries(self.store),
                                 (None, 'unreadable'))
                self.assertEqual(task_index.TaskStatusIndex(self.store).entries, {})
        unreadable = {'stat': entry['stat'], 'readable': False, 'created_at': None,
                      'pm_session_id': None, 'tui': entry['tui']}
        self.assertFalse(task_index._valid_entry(self.task_id, unreadable))

    def test_display_entries_reasons_and_read_only(self):
        self.assertEqual(task_index.display_entries(self.store), (None, 'missing'))
        self.assertFalse(self.path.exists())
        self.saved()
        data = json.loads(self.path.read_text())
        data['fingerprint'] = '0' * 64
        self.path.write_text(json.dumps(data))
        self.assertEqual(task_index.display_entries(self.store), (None, 'stale'))
        self.path.write_text('{not json')
        self.assertEqual(task_index.display_entries(self.store), (None, 'unreadable'))
        os.chmod(self.path, 0o644)
        self.assertEqual(task_index.display_entries(self.store), (None, 'unreadable'))
        os.chmod(self.state, 0o777)
        try:
            self.assertEqual(task_index.display_entries(self.store), (None, 'unavailable'))
        finally:
            os.chmod(self.state, 0o700)
        self.assertEqual(task_index.display_entries(object()), (None, 'unavailable'))
        self.assertEqual(sorted(p.name for p in self.state.iterdir()),
                         ['task-status-index.json', 'tasks'])


if __name__ == '__main__':
    unittest.main()
