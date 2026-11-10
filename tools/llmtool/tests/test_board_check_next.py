"""Tests for optional row field 'next' in board reconciliation (#763).

Covers DONE row with missing next ID, next ID present on the board,
invalid next shapes classified as ANOMALY, and unchanged rows without next.
"""

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import board_check  # noqa: E402


class FakeStore:
    """Minimal fake TaskStore for board check tests."""

    def __init__(self, tasks=None):
        self._tasks = dict(tasks or {})

    def task_ids(self):
        return list(self._tasks.keys())

    def get(self, task_id):
        if task_id not in self._tasks:
            raise KeyError(task_id)
        return dict(self._tasks[task_id])


def _make_github(pulls=None):
    pull_data = pulls or {}

    def github_get(path):
        if path == 'pulls?state=open&per_page=100&page=1':
            return []
        if path.startswith('pulls/'):
            number = int(path.partition('pulls/')[2])
            if number in pull_data:
                return pull_data[number]
            raise KeyError(f'unknown pull {number}')
        if '/check-runs?' in path:
            return {'total_count': 0, 'check_runs': []}
        raise KeyError(path)

    return github_get


def _fake_list_processes():
    return []


def _fake_evaluate(worker, owner_ref=None, task_id=None):
    class Decision:
        allowed = True
        reason_code = 'ALLOWED'

    return Decision()


class BoardCheckNextTests(unittest.TestCase):
    """Test suite for row field 'next' handling."""

    def setUp(self):
        self.store = FakeStore()
        self.now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
        self.head_sha = '1' * 40

    def test_done_row_with_missing_next_id_gives_pending_finding(self):
        """A DONE row listing a missing next ID adds one ACTIONABLE finding."""
        rows = [
            {
                'id': 'step-1',
                'pr': 101,
                'next': ['step-2'],
            },
        ]
        pulls = {
            101: {
                'merged': True,
                'state': 'closed',
                'head': {'sha': self.head_sha},
            },
        }
        res = board_check.check_board(
            rows,
            store=self.store,
            github_get=_make_github(pulls),
            list_processes=_fake_list_processes,
            evaluate=_fake_evaluate,
            now=self.now,
        )

        self.assertEqual(res['result'], 'FLAGGED')
        self.assertEqual(res['exit_code'], 2)

        step1_finding = next(r for r in res['rows'] if r['row'] == 'step-1')
        self.assertEqual(step1_finding['state'], 'DONE')

        next_finding = next(r for r in res['rows'] if r['row'] == 'next:step-2')
        self.assertEqual(next_finding['state'], 'ACTIONABLE')
        self.assertEqual(next_finding['evidence'], ['pending next item of step-1'])

    def test_done_row_with_multiple_missing_next_ids(self):
        """A DONE row with multiple missing next IDs adds one finding for each."""
        rows = [
            {
                'id': 'step-1',
                'pr': 101,
                'next': ['step-2', 'step-3'],
            },
        ]
        pulls = {
            101: {
                'merged': True,
                'state': 'closed',
                'head': {'sha': self.head_sha},
            },
        }
        res = board_check.check_board(
            rows,
            store=self.store,
            github_get=_make_github(pulls),
            list_processes=_fake_list_processes,
            evaluate=_fake_evaluate,
            now=self.now,
        )

        self.assertEqual(res['exit_code'], 2)
        next2 = next(r for r in res['rows'] if r['row'] == 'next:step-2')
        self.assertEqual(next2['state'], 'ACTIONABLE')
        self.assertEqual(next2['evidence'], ['pending next item of step-1'])

        next3 = next(r for r in res['rows'] if r['row'] == 'next:step-3')
        self.assertEqual(next3['state'], 'ACTIONABLE')
        self.assertEqual(next3['evidence'], ['pending next item of step-1'])

    def test_next_id_present_on_board_classified_on_its_own(self):
        """A listed next ID that is present on the board is classified on its own."""
        rows = [
            {
                'id': 'step-1',
                'pr': 101,
                'next': ['step-2'],
            },
            {
                'id': 'step-2',
                'pr': 102,
            },
        ]
        pulls = {
            101: {
                'merged': True,
                'state': 'closed',
                'head': {'sha': self.head_sha},
            },
            102: {
                'merged': False,
                'state': 'open',
                'head': {'sha': '2' * 40},
            },
        }
        res = board_check.check_board(
            rows,
            store=self.store,
            github_get=_make_github(pulls),
            list_processes=_fake_list_processes,
            evaluate=_fake_evaluate,
            now=self.now,
        )

        step1 = next(r for r in res['rows'] if r['row'] == 'step-1')
        self.assertEqual(step1['state'], 'DONE')

        step2 = next(r for r in res['rows'] if r['row'] == 'step-2')
        self.assertNotEqual(step2['state'], 'DONE')

        next_findings = [r for r in res['rows'] if r['row'].startswith('next:')]
        self.assertEqual(next_findings, [])

    def test_invalid_next_shapes_classified_as_anomaly(self):
        """Any next field that is not 1-16 strings of 1-64 chars is ANOMALY."""
        invalid_shapes = [
            'not-a-list',
            123,
            None,
            [],
            [''],
            ['a' * 65],
            ['ok'] * 17,
            [123],
            ['valid', None],
        ]

        for shape in invalid_shapes:
            with self.subTest(shape=shape):
                rows = [
                    {
                        'id': 'row-1',
                        'pr': 101,
                        'next': shape,
                    },
                ]
                pulls = {
                    101: {
                        'merged': True,
                        'state': 'closed',
                        'head': {'sha': self.head_sha},
                    },
                }
                res = board_check.check_board(
                    rows,
                    store=self.store,
                    github_get=_make_github(pulls),
                    list_processes=_fake_list_processes,
                    evaluate=_fake_evaluate,
                    now=self.now,
                )

                self.assertEqual(res['result'], 'FLAGGED')
                self.assertEqual(res['exit_code'], 2)
                row_finding = next(r for r in res['rows'] if r['row'] == 'row-1')
                self.assertEqual(row_finding['state'], 'ANOMALY')
                self.assertEqual(row_finding['evidence'], ['invalid next list'])

    def test_no_next_unchanged(self):
        """Rows without next field classify exactly as before."""
        rows = [
            {
                'id': 'step-1',
                'pr': 101,
            },
        ]
        pulls = {
            101: {
                'merged': True,
                'state': 'closed',
                'head': {'sha': self.head_sha},
            },
        }
        res = board_check.check_board(
            rows,
            store=self.store,
            github_get=_make_github(pulls),
            list_processes=_fake_list_processes,
            evaluate=_fake_evaluate,
            now=self.now,
        )

        self.assertEqual(res['result'], 'CLEAN')
        self.assertEqual(res['exit_code'], 0)
        row_finding = next(r for r in res['rows'] if r['row'] == 'step-1')
        self.assertEqual(row_finding['state'], 'DONE')

    def test_not_done_row_with_next_does_not_emit_pending_finding(self):
        """A row that is not DONE does not emit pending next findings."""
        rows = [
            {
                'id': 'step-1',
                'pr': 102,
                'next': ['missing-step'],
            },
        ]
        pulls = {
            102: {
                'merged': False,
                'state': 'open',
                'head': {'sha': self.head_sha},
            },
        }
        res = board_check.check_board(
            rows,
            store=self.store,
            github_get=_make_github(pulls),
            list_processes=_fake_list_processes,
            evaluate=_fake_evaluate,
            now=self.now,
        )

        row_finding = next(r for r in res['rows'] if r['row'] == 'step-1')
        self.assertNotEqual(row_finding['state'], 'DONE')
        next_findings = [r for r in res['rows'] if r['row'] == 'next:missing-step']
        self.assertEqual(next_findings, [])
