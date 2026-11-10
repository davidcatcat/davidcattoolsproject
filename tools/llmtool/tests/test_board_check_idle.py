"""Idle-runnable anomaly tests for check_board (#763).

Covers IDLE_RUNNABLE anomalies: an ACTIONABLE row with no live ledger
claim is listed, a row with a live reservation or bound Task claim is
not, and a None ledger adds no 'anomalies' key.
"""

import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import board_check


class _FakeStore:
    """Read-only store stub for board check tests."""

    def __init__(self, tasks=None):
        self.tasks = dict(tasks or {})

    def get(self, task_id):
        if task_id not in self.tasks:
            raise KeyError(task_id)
        return dict(self.tasks[task_id])

    def task_ids(self):
        return list(self.tasks.keys())


def _fake_github_reader(pulls=None):
    """Return a fake GitHub read adapter."""
    pulls_data = dict(pulls or {})

    def get(path):
        if path.startswith('pulls?state=open'):
            return [{'number': num} for num, p in pulls_data.items() if p.get('state') == 'open']
        if path.startswith('pulls/'):
            num = int(path.split('/')[1])
            if num in pulls_data:
                return dict(pulls_data[num])
            return {'number': num, 'state': 'open', 'merged': False, 'head': {'sha': '0' * 40}}
        if path.startswith('commits/'):
            return {'check_runs': [], 'total_count': 0}
        if path.startswith('issues/'):
            return {'state': 'open'}
        return []

    return get


def _fake_list_processes():
    return []


def _fake_evaluate(worker, owner_ref=None, task_id=None):
    return types.SimpleNamespace(allowed=True, reason_code='ALLOWED')


class BoardCheckIdleTests(unittest.TestCase):
    """Tests for check_board IDLE_RUNNABLE anomalies."""

    def setUp(self):
        self.now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
        self.github_get = _fake_github_reader()
        self.list_procs = _fake_list_processes
        self.evaluate = _fake_evaluate

    def _run(self, rows, store, claims, now=None):
        """Call check_board with the shared fakes and the given claims."""
        return board_check.check_board(
            rows,
            store=store,
            github_get=self.github_get,
            list_processes=self.list_procs,
            evaluate=self.evaluate,
            now=now or self.now,
            ledger_claims=claims,
        )

    def test_actionable_row_without_claim_is_listed(self):
        """An ACTIONABLE row with no live ledger claim is an IDLE_RUNNABLE anomaly."""
        task_id = '7' * 32
        store = _FakeStore({task_id: {'task_id': task_id, 'state': 'PM_PENDING', 'developer': None}})
        rows = [{'id': 'row-1', 'task_id': task_id}]

        res = self._run(rows, store, [])

        # Today's classification for this row is ACTIONABLE -> FLAGGED, exit 2.
        self.assertEqual(res['rows'][0]['state'], board_check.ACTIONABLE)
        self.assertEqual(res['result'], 'FLAGGED')
        self.assertEqual(res['exit_code'], 2)
        self.assertEqual(
            res['anomalies'],
            [{'row': res['rows'][0]['row'], 'code': 'IDLE_RUNNABLE'}],
        )

    def test_live_reservation_row_not_listed(self):
        """The same row with a live reservation is never listed."""
        task_id = '8' * 32
        store = _FakeStore({task_id: {'task_id': task_id, 'state': 'PM_PENDING', 'developer': None}})
        rows = [{'id': 'row-1', 'task_id': task_id}]
        claim = {
            'holder': 'pm-1',
            'row': 'row-1',
            'claimed_at': '2026-11-10T12:00:00Z',
            'task_id': None,
            'bound_at': None,
        }
        t1 = self.now + timedelta(minutes=5)

        res = self._run(rows, store, [claim], now=t1)

        # The reservation is still inside the TTL, so the row has a live claim.
        self.assertEqual(res['anomalies'], [])

    def test_live_bound_claim_row_not_listed(self):
        """The same row with a live bound Task claim is never listed."""
        task_id = '9' * 32
        store = _FakeStore({task_id: {'task_id': task_id, 'state': 'PM_PENDING', 'developer': None}})
        rows = [{'id': 'row-1', 'task_id': task_id}]
        claim = {
            'holder': 'pm-1',
            'row': 'row-1',
            'claimed_at': '2026-11-10T12:00:00Z',
            'task_id': task_id,
            'bound_at': '2026-11-10T12:01:00Z',
        }

        res = self._run(rows, store, [claim])

        # The bound Task is not terminal, so the claim stays live.
        self.assertEqual(res['anomalies'], [])

    def test_ledger_none_has_no_anomalies_key(self):
        """ledger_claims=None returns today's dict without an 'anomalies' key."""
        task_id = 'b' * 32
        store = _FakeStore({task_id: {'task_id': task_id, 'state': 'PM_PENDING', 'developer': None}})
        rows = [{'id': 'row-1', 'task_id': task_id}]

        res_none = self._run(rows, store, None)
        res_default = board_check.check_board(
            rows,
            store=store,
            github_get=self.github_get,
            list_processes=self.list_procs,
            evaluate=self.evaluate,
            now=self.now,
        )

        self.assertNotIn('anomalies', res_none)
        self.assertEqual(res_none, res_default)
        self.assertEqual(res_none['result'], 'FLAGGED')
        self.assertEqual(res_none['exit_code'], 2)
