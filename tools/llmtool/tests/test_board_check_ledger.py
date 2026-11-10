"""Tests for board reconciliation with ledger claims (#763).

Covers bound claims, live reservations, stale claims, task mismatch,
ledger None, and unreadable ledger handling.
"""

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
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


class BoardCheckLedgerTests(unittest.TestCase):
    """Tests for board_check ledger claim classification."""

    def setUp(self):
        self.now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
        self.github_get = _fake_github_reader()
        self.list_procs = _fake_list_processes
        self.evaluate = _fake_evaluate

    def test_bound_claim_classifies_via_task(self):
        """A live bound claim classifies a row without task_id exactly as that Task."""
        task_id = '1' * 32
        task_record = {
            'task_id': task_id,
            'state': 'PM_PENDING',
            'developer': None,
        }
        store = _FakeStore({task_id: task_record})

        row_no_task = {'id': 'row-1'}
        row_with_task = {'id': 'row-1', 'task_id': task_id}

        # Baseline: classify row with task_id and no ledger.
        expected = board_check.check_board(
            [row_with_task],
            store=store,
            github_get=self.github_get,
            list_processes=self.list_procs,
            evaluate=self.evaluate,
            now=self.now,
            ledger_claims=None,
        )

        claim = {
            'holder': 'pm-1',
            'row': 'row-1',
            'claimed_at': '2026-11-10T12:00:00Z',
            'task_id': task_id,
            'bound_at': '2026-11-10T12:01:00Z',
        }
        actual = board_check.check_board(
            [row_no_task],
            store=store,
            github_get=self.github_get,
            list_processes=self.list_procs,
            evaluate=self.evaluate,
            now=self.now,
            ledger_claims=[claim],
        )

        self.assertEqual(actual['rows'][0]['state'], expected['rows'][0]['state'])
        self.assertEqual(actual['rows'][0]['evidence'], expected['rows'][0]['evidence'])
        self.assertEqual(actual['result'], expected['result'])

    def test_live_reservation_claimed(self):
        """A live reservation without a Task gives CLAIMED with 'live reservation'."""
        store = _FakeStore()
        row = {'id': 'row-1'}
        claim = {
            'holder': 'pm-1',
            'row': 'row-1',
            'claimed_at': '2026-11-10T12:00:00Z',
            'task_id': None,
            'bound_at': None,
        }
        t1 = self.now + timedelta(minutes=5)
        res = board_check.check_board(
            [row],
            store=store,
            github_get=self.github_get,
            list_processes=self.list_procs,
            evaluate=self.evaluate,
            now=t1,
            ledger_claims=[claim],
        )
        self.assertEqual(res['rows'][0]['state'], board_check.CLAIMED)
        self.assertEqual(res['rows'][0]['evidence'], ['live reservation'])
        self.assertEqual(res['result'], 'CLEAN')
        self.assertEqual(res['exit_code'], 0)

    def test_stale_claim_anomaly_expired_reservation(self):
        """An expired reservation gives ANOMALY 'stale ledger claim'."""
        store = _FakeStore()
        row = {'id': 'row-1'}
        claim = {
            'holder': 'pm-1',
            'row': 'row-1',
            'claimed_at': '2026-11-10T11:00:00Z',
            'task_id': None,
            'bound_at': None,
        }
        res = board_check.check_board(
            [row],
            store=store,
            github_get=self.github_get,
            list_processes=self.list_procs,
            evaluate=self.evaluate,
            now=self.now,
            ledger_claims=[claim],
        )
        self.assertEqual(res['rows'][0]['state'], board_check.ANOMALY)
        self.assertEqual(res['rows'][0]['evidence'], ['stale ledger claim'])
        self.assertEqual(res['result'], 'FLAGGED')
        self.assertEqual(res['exit_code'], 2)

    def test_stale_claim_anomaly_dead_bound_task(self):
        """A bound claim on an abandoned task gives ANOMALY 'stale ledger claim'."""
        task_id = '2' * 32
        store = _FakeStore({task_id: {'task_id': task_id, 'state': 'ABANDONED'}})
        row = {'id': 'row-1'}
        claim = {
            'holder': 'pm-1',
            'row': 'row-1',
            'claimed_at': '2026-11-10T12:00:00Z',
            'task_id': task_id,
            'bound_at': '2026-11-10T12:01:00Z',
        }
        res = board_check.check_board(
            [row],
            store=store,
            github_get=self.github_get,
            list_processes=self.list_procs,
            evaluate=self.evaluate,
            now=self.now,
            ledger_claims=[claim],
        )
        self.assertEqual(res['rows'][0]['state'], board_check.ANOMALY)
        self.assertEqual(res['rows'][0]['evidence'], ['stale ledger claim'])
        self.assertEqual(res['result'], 'FLAGGED')
        self.assertEqual(res['exit_code'], 2)

    def test_task_mismatch_anomaly(self):
        """A row with task_id gives ANOMALY if a live claim binds a different Task."""
        task_a = '3' * 32
        task_b = '4' * 32
        store = _FakeStore({
            task_a: {'task_id': task_a, 'state': 'PM_PENDING', 'developer': None},
            task_b: {'task_id': task_b, 'state': 'PM_PENDING', 'developer': None},
        })
        row = {'id': 'row-1', 'task_id': task_a}
        claim_diff = {
            'holder': 'pm-1',
            'row': 'row-1',
            'claimed_at': '2026-11-10T12:00:00Z',
            'task_id': task_b,
            'bound_at': '2026-11-10T12:01:00Z',
        }
        res = board_check.check_board(
            [row],
            store=store,
            github_get=self.github_get,
            list_processes=self.list_procs,
            evaluate=self.evaluate,
            now=self.now,
            ledger_claims=[claim_diff],
        )
        self.assertEqual(res['rows'][0]['state'], board_check.ANOMALY)
        self.assertEqual(res['rows'][0]['evidence'], ['board task differs from ledger claim'])
        self.assertEqual(res['result'], 'FLAGGED')
        self.assertEqual(res['exit_code'], 2)

        # When the claim binds the same Task, classification is unchanged.
        claim_same = dict(claim_diff, task_id=task_a)
        res_same = board_check.check_board(
            [row],
            store=store,
            github_get=self.github_get,
            list_processes=self.list_procs,
            evaluate=self.evaluate,
            now=self.now,
            ledger_claims=[claim_same],
        )
        self.assertNotEqual(res_same['rows'][0]['state'], board_check.ANOMALY)

    def test_ledger_none_unchanged(self):
        """Passing ledger_claims=None yields the exact same results as default."""
        task_id = '5' * 32
        store = _FakeStore({task_id: {'task_id': task_id, 'state': 'PM_PENDING', 'developer': None}})
        rows = [{'id': 'row-1'}, {'id': 'row-2', 'task_id': task_id}]

        res_none = board_check.check_board(
            rows,
            store=store,
            github_get=self.github_get,
            list_processes=self.list_procs,
            evaluate=self.evaluate,
            now=self.now,
            ledger_claims=None,
        )
        res_default = board_check.check_board(
            rows,
            store=store,
            github_get=self.github_get,
            list_processes=self.list_procs,
            evaluate=self.evaluate,
            now=self.now,
        )
        self.assertEqual(res_none, res_default)

    def test_unreadable_ledger_unknown(self):
        """An unreadable ledger makes rows without task_id UNKNOWN with LEDGER_UNREADABLE."""
        task_id = '6' * 32
        store = _FakeStore({task_id: {'task_id': task_id, 'state': 'PM_PENDING', 'developer': None}})
        rows = [{'id': 'row-1'}, {'id': 'row-2', 'task_id': task_id}]

        # Direct check_board with ledger_unreadable=True.
        res = board_check.check_board(
            rows,
            store=store,
            github_get=self.github_get,
            list_processes=self.list_procs,
            evaluate=self.evaluate,
            now=self.now,
            ledger_unreadable=True,
        )
        self.assertEqual(res['rows'][0]['state'], board_check.UNKNOWN)
        self.assertEqual(res['rows'][0]['evidence'], ['LEDGER_UNREADABLE'])
        # Row with task_id classified as today. row-2 is ACTIONABLE (flagged),
        # so check_board ranks FLAGGED (exit 2) above UNKNOWN (exit 1).
        self.assertNotEqual(res['rows'][1]['state'], board_check.UNKNOWN)
        self.assertEqual(res['exit_code'], 2)

        # run_board_check with a ledger that raises LedgerUnreadable.
        class _UnreadableLedger:
            def read(self):
                raise board_check.LedgerUnreadable('ledger corrupted')

        with tempfile.TemporaryDirectory() as tmp_dir:
            board_file = Path(tmp_dir) / 'pm-board.json'
            board_file.write_text(
                json.dumps({'schema_version': 1, 'rows': rows}),
                encoding='utf-8',
            )
            args = argparse.Namespace(
                board=str(board_file),
                json=True,
                claim=None,
            )
            out_buf = io.StringIO()
            with contextlib.redirect_stdout(out_buf):
                code = board_check.run_board_check(
                    args,
                    store=store,
                    github_get=self.github_get,
                    list_processes_call=self.list_procs,
                    evaluate=self.evaluate,
                    now=self.now,
                    ledger=_UnreadableLedger(),
                )
            # Exit code is 2 because row-2 is flagged (ACTIONABLE).
            self.assertEqual(code, 2)
            parsed = json.loads(out_buf.getvalue())
            self.assertEqual(parsed['rows'][0]['state'], board_check.UNKNOWN)
            self.assertEqual(parsed['rows'][0]['evidence'], ['LEDGER_UNREADABLE'])
            self.assertNotEqual(parsed['rows'][1]['state'], board_check.UNKNOWN)
