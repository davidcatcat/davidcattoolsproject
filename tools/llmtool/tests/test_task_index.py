"""#1838: the task status index keeps an optional TUI projection of each readable record."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import active_pm_inbox, chat_tui_status, task_index, task_submission_cli
from llmtool.errors import LLMToolError


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

    def test_inbox_projection_states_and_validation(self):
        entry = self.saved()
        self.assertEqual(entry['inbox'], task_index.INBOX_NONE)

        active_base = {
            'backend_session_semantics': 'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
            'pm_provenance': {'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False},
            'pm_session_id': 'pm-session-1',
        }
        self.assertEqual(task_index._inbox_summary(_record(self.task_id, **active_base)),
                         task_index.INBOX_NONE)
        self.assertEqual(task_index._inbox_summary(_record(
            self.task_id, **active_base,
            completion_events=[{'event_id': 'e' * 32, 'state': 'PENDING'}])),
            task_index.INBOX_PENDING)
        self.assertEqual(task_index._inbox_summary(_record(
            self.task_id, **active_base,
            completion_events=[{'event_id': 'e' * 32, 'state': 'ACKED'}])),
            task_index.INBOX_IDLE)
        self.assertEqual(task_index._inbox_summary(_record(
            self.task_id, **active_base,
            completion_event_tombstones=[{'event_id': 'e' * 32}])),
            task_index.INBOX_IDLE)

        with patch.object(task_submission_cli, '_active_pm_handoff',
                          return_value={'packet': {'synthetic': True}}), \
             patch.object(active_pm_inbox, '_handoff_event',
                          return_value={'event_id': 'f' * 32}):
            self.assertEqual(
                task_index._inbox_summary(_record(self.task_id, **active_base)),
                task_index.INBOX_PENDING)

        for bad_inbox in ('OTHER', 123, None):
            broken = dict(entry, inbox=bad_inbox)
            self.assertFalse(task_index._valid_entry(self.task_id, broken))
        unreadable_with_inbox = {
            'stat': entry['stat'], 'readable': False, 'created_at': None,
            'pm_session_id': None, 'inbox': task_index.INBOX_NONE,
        }
        self.assertFalse(task_index._valid_entry(self.task_id, unreadable_with_inbox))

    def test_scan_inbox_skips_none_and_idle_and_fails_closed(self):
        class CountingStore(_Store):
            def __init__(self, root, records):
                super().__init__(root, records)
                self.gets = []
                self.unreadable = set()

            def get(self, task_id):
                self.gets.append(task_id)
                if task_id in self.unreadable:
                    raise LLMToolError('unreadable synthetic record')
                return super().get(task_id)

        active_base = {
            'backend_session_semantics': 'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
            'pm_provenance': {'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False},
        }
        id_none = '1' * 32
        id_idle = '2' * 32
        id_pending = '3' * 32
        id_other = '4' * 32
        records = {
            id_none: _record(id_none, pm_session_id='pm-one', **active_base),
            id_idle: _record(id_idle, pm_session_id='pm-one', **active_base,
                             completion_events=[{'event_id': 'a' * 32, 'state': 'ACKED'}]),
            id_pending: _record(id_pending, pm_session_id='pm-one', **active_base,
                                completion_events=[{'event_id': 'b' * 32, 'state': 'PENDING'}]),
            id_other: _record(id_other, pm_session_id='pm-two', **active_base,
                              completion_events=[{'event_id': 'c' * 32, 'state': 'PENDING'}]),
        }
        for tid in records:
            (self.root / (tid + '.json')).write_text('{}')
        store = CountingStore(self.root, records)

        # Cold scan reads every record and persists the index.
        index = task_index.TaskStatusIndex(store)
        found, unreadable = index.scan_inbox(sorted(records), 'pm-one', include_idle=False)
        index.flush()
        self.assertEqual(unreadable, 0)
        self.assertEqual([r['task_id'] for r in found], [id_none, id_idle, id_pending])
        self.assertEqual(sorted(store.gets), sorted(records))

        # Warm scan skips NONE, IDLE and other-session records, and does not dirty index.
        store.gets.clear()
        warm = task_index.TaskStatusIndex(store)
        found, unreadable = warm.scan_inbox(sorted(records), 'pm-one', include_idle=False)
        self.assertEqual(unreadable, 0)
        self.assertEqual([r['task_id'] for r in found], [id_pending])
        self.assertEqual(store.gets, [id_pending])
        self.assertFalse(warm.changed)

        # include_idle=True includes IDLE as well as PENDING.
        store.gets.clear()
        found_idle, unreadable = warm.scan_inbox(sorted(records), 'pm-one', include_idle=True)
        self.assertEqual(unreadable, 0)
        self.assertEqual([r['task_id'] for r in found_idle], [id_idle, id_pending])
        self.assertEqual(store.gets, [id_idle, id_pending])

        # Missing 'inbox' projection fails closed with a validated read.
        data = json.loads(self.path.read_text())
        data['entries'][id_none].pop('inbox', None)
        self.path.write_text(json.dumps(data))
        store.gets.clear()
        reloaded = task_index.TaskStatusIndex(store)
        found, _ = reloaded.scan_inbox(sorted(records), 'pm-one', include_idle=False)
        self.assertEqual([r['task_id'] for r in found], [id_none, id_pending])
        self.assertEqual(store.gets, [id_none, id_pending])

        # A changed/damaged record is detected via stat and reported unreadable.
        (self.root / (id_none + '.json')).write_text('{"corrupt":true}')
        store.unreadable.add(id_none)
        store.gets.clear()
        damaged = task_index.TaskStatusIndex(store)
        found, unreadable = damaged.scan_inbox(sorted(records), 'pm-one', include_idle=False)
        self.assertEqual(unreadable, 1)
        self.assertEqual([r['task_id'] for r in found], [id_pending])
        self.assertEqual(store.gets, [id_none, id_pending])


if __name__ == '__main__':
    unittest.main()
