"""Unit tests for board row claims and bindings (#763).

Covers claim_live, claim_row, ledger persistence, reservation expiry,
task binding, and CLI integration with exit codes.
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import board_check  # noqa: E402
from llmtool.task_store import TaskStore  # noqa: E402
from llmtool.task_verification import parse_task_contract  # noqa: E402


class BoardClaimTests(unittest.TestCase):
    """Tests for claim_live, claim_row and CLI --claim flags."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.orig_home = os.environ.get('HOME')
        os.environ['HOME'] = str(self.home)

        self.state_dir = self.home / '.local' / 'state' / 'llmtool'
        self.state_dir.mkdir(parents=True, mode=0o700)

        self.board_path = self.state_dir / 'pm-board.json'
        self.rows = [{'id': 'row-1'}, {'id': 'row-2'}]
        self.board_path.write_text(
            json.dumps({'schema_version': 1, 'rows': self.rows}),
            encoding='utf-8',
        )
        os.chmod(self.board_path, 0o600)

        self.ledger = board_check.BoardLedger()
        self.tasks = {}
        tasks = self.tasks

        class _Store:
            def get(self, task_id):
                if task_id not in tasks:
                    raise KeyError(task_id)
                return dict(tasks[task_id])

            def abandon(self, task_id, **_kwargs):
                tasks[task_id]['state'] = 'ABANDONED'

        self.store = _Store()
        self.now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        if self.orig_home is not None:
            os.environ['HOME'] = self.orig_home
        else:
            os.environ.pop('HOME', None)
        self.tmp.cleanup()

    def _create_task(self, task_id, run_id, state='PM_PENDING'):
        self.tasks[task_id] = {'task_id': task_id, 'run_id': run_id, 'state': state}
        return dict(self.tasks[task_id])

    def test_reserve_then_bind(self):
        """A row is reserved first and then bound to a stored task."""
        claim = board_check.claim_row(
            self.ledger, self.rows, 'row-1', 'pm-1',
            task_id=None, store=self.store, now=self.now,
        )
        self.assertEqual(claim['holder'], 'pm-1')
        self.assertEqual(claim['row'], 'row-1')
        self.assertIsNone(claim['task_id'])
        self.assertIsNone(claim['bound_at'])
        self.assertTrue(board_check.claim_live(claim, self.store, self.now))

        task_id = '1' * 32
        run_id = '2' * 32
        self._create_task(task_id, run_id)

        t1 = self.now + timedelta(minutes=5)
        bound = board_check.claim_row(
            self.ledger, self.rows, 'row-1', 'pm-1',
            task_id=task_id, store=self.store, now=t1,
        )
        self.assertEqual(bound['task_id'], task_id)
        self.assertEqual(bound['bound_at'], '2026-11-10T12:05:00Z')
        self.assertTrue(board_check.claim_live(bound, self.store, t1))

        claims = self.ledger.read()
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['task_id'], task_id)

    def test_second_claim_refused_claim_held(self):
        """A second claim on an already-claimed row is refused CLAIM_HELD."""
        board_check.claim_row(
            self.ledger, self.rows, 'row-1', 'pm-1',
            task_id=None, store=self.store, now=self.now,
        )
        with self.assertRaises(board_check.ClaimRefused) as cm:
            board_check.claim_row(
                self.ledger, self.rows, 'row-1', 'pm-1',
                task_id=None, store=self.store, now=self.now,
            )
        self.assertEqual(cm.exception.code, 'CLAIM_HELD')

        with self.assertRaises(board_check.ClaimRefused) as cm:
            board_check.claim_row(
                self.ledger, self.rows, 'row-1', 'pm-2',
                task_id=None, store=self.store, now=self.now,
            )
        self.assertEqual(cm.exception.code, 'CLAIM_HELD')

    def test_expired_reservation_can_be_claimed_again(self):
        """An expired reservation becomes not live and allows a new claim."""
        old = board_check.claim_row(
            self.ledger, self.rows, 'row-1', 'pm-1',
            task_id=None, store=self.store, now=self.now,
        )
        t_expired = self.now + board_check.RESERVATION_TTL + timedelta(seconds=1)
        self.assertFalse(board_check.claim_live(old, self.store, t_expired))

        new_claim = board_check.claim_row(
            self.ledger, self.rows, 'row-1', 'pm-2',
            task_id=None, store=self.store, now=t_expired,
        )
        self.assertEqual(new_claim['holder'], 'pm-2')
        self.assertTrue(board_check.claim_live(new_claim, self.store, t_expired))

        claims = self.ledger.read()
        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0]['holder'], 'pm-1')
        self.assertEqual(claims[1]['holder'], 'pm-2')

    def test_fake_missing_and_abandoned_bound_task_not_live(self):
        """Bound claims with fake, missing or abandoned tasks are not live."""
        fake_claim = {
            'holder': 'pm-1',
            'row': 'row-1',
            'claimed_at': '2026-11-10T12:00:00Z',
            'task_id': 'bad-id',
            'bound_at': '2026-11-10T12:01:00Z',
        }
        self.assertFalse(board_check.claim_live(fake_claim, self.store, self.now))

        missing_claim = {
            'holder': 'pm-1',
            'row': 'row-1',
            'claimed_at': '2026-11-10T12:00:00Z',
            'task_id': '3' * 32,
            'bound_at': '2026-11-10T12:01:00Z',
        }
        self.assertFalse(board_check.claim_live(missing_claim, self.store, self.now))

        task_id = '4' * 32
        run_id = '5' * 32
        self._create_task(task_id, run_id)
        board_check.claim_row(
            self.ledger, self.rows, 'row-1', 'pm-1',
            task_id=None, store=self.store, now=self.now,
        )
        bound = board_check.claim_row(
            self.ledger, self.rows, 'row-1', 'pm-1',
            task_id=task_id, store=self.store, now=self.now,
        )
        self.assertTrue(board_check.claim_live(bound, self.store, self.now))

        self.store.abandon(task_id, pm_session_id='pm-test-session', reason='abandoned in test')
        self.assertFalse(board_check.claim_live(bound, self.store, self.now))

        reclaimed = board_check.claim_row(
            self.ledger, self.rows, 'row-1', 'pm-2',
            task_id=None, store=self.store, now=self.now,
        )
        self.assertEqual(reclaimed['holder'], 'pm-2')

    def test_task_for_another_holder_refused(self):
        """Binding a task to a reservation owned by another holder is refused."""
        board_check.claim_row(
            self.ledger, self.rows, 'row-1', 'pm-1',
            task_id=None, store=self.store, now=self.now,
        )
        task_id = '6' * 32
        run_id = '7' * 32
        self._create_task(task_id, run_id)

        with self.assertRaises(board_check.ClaimRefused) as cm:
            board_check.claim_row(
                self.ledger, self.rows, 'row-1', 'pm-2',
                task_id=task_id, store=self.store, now=self.now,
            )
        self.assertEqual(cm.exception.code, 'CLAIM_NOT_RESERVED')

    def test_two_claim_calls_in_a_row_give_one_winner(self):
        """Consecutive claims for the same row produce one winner and one refusal."""
        first = board_check.claim_row(
            self.ledger, self.rows, 'row-1', 'pm-1',
            task_id=None, store=self.store, now=self.now,
        )
        self.assertEqual(first['holder'], 'pm-1')

        with self.assertRaises(board_check.ClaimRefused) as cm:
            board_check.claim_row(
                self.ledger, self.rows, 'row-1', 'pm-2',
                task_id=None, store=self.store, now=self.now,
            )
        self.assertEqual(cm.exception.code, 'CLAIM_HELD')

        claims = self.ledger.read()
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['holder'], 'pm-1')

    def test_claim_row_unknown(self):
        """Attempting to claim an unlisted row id raises CLAIM_ROW_UNKNOWN."""
        with self.assertRaises(board_check.ClaimRefused) as cm:
            board_check.claim_row(
                self.ledger, self.rows, 'non-existent-row', 'pm-1',
                task_id=None, store=self.store, now=self.now,
            )
        self.assertEqual(cm.exception.code, 'CLAIM_ROW_UNKNOWN')

    def test_claim_task_bound_elsewhere_refused(self):
        """Attempting to bind a task already bound to another live claim is refused."""
        task_id = '8' * 32
        run_id = '9' * 32
        self._create_task(task_id, run_id)

        board_check.claim_row(
            self.ledger, self.rows, 'row-1', 'pm-1',
            task_id=None, store=self.store, now=self.now,
        )
        board_check.claim_row(
            self.ledger, self.rows, 'row-1', 'pm-1',
            task_id=task_id, store=self.store, now=self.now,
        )

        board_check.claim_row(
            self.ledger, self.rows, 'row-2', 'pm-1',
            task_id=None, store=self.store, now=self.now,
        )
        with self.assertRaises(board_check.ClaimRefused) as cm:
            board_check.claim_row(
                self.ledger, self.rows, 'row-2', 'pm-1',
                task_id=task_id, store=self.store, now=self.now,
            )
        self.assertEqual(cm.exception.code, 'CLAIM_TASK_BOUND')

    def test_cli_exit_codes(self):
        """CLI returns exit code 0 on success, 2 on missing holder, 3 on refusal."""
        out_buf = io.StringIO()
        err_buf = io.StringIO()

        # Exit code 0: successful claim
        args0 = argparse.Namespace(
            claim='row-1', holder='pm-1', task=None,
            board=str(self.board_path), json=False,
        )
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            code0 = board_check.run_board_check(
                args0, store=self.store, ledger=self.ledger, now=self.now,
            )
        self.assertEqual(code0, 0)
        self.assertEqual(out_buf.getvalue().strip(), 'claimed row-1 for pm-1')

        # Exit code 2: missing holder with --claim
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        args2 = argparse.Namespace(
            claim='row-1', holder=None, task=None,
            board=str(self.board_path), json=False,
        )
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            code2 = board_check.run_board_check(
                args2, store=self.store, ledger=self.ledger, now=self.now,
            )
        self.assertEqual(code2, 2)
        self.assertIn('--claim requires --holder', err_buf.getvalue())

        # Exit code 3: refusal on unknown row
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        args3 = argparse.Namespace(
            claim='unknown-row', holder='pm-1', task=None,
            board=str(self.board_path), json=False,
        )
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            code3 = board_check.run_board_check(
                args3, store=self.store, ledger=self.ledger, now=self.now,
            )
        self.assertEqual(code3, 3)
        self.assertEqual(out_buf.getvalue().strip(), 'CLAIM_ROW_UNKNOWN')

        # Exit code 3: refusal on held row
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        args3_held = argparse.Namespace(
            claim='row-1', holder='pm-2', task=None,
            board=str(self.board_path), json=False,
        )
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            code3_held = board_check.run_board_check(
                args3_held, store=self.store, ledger=self.ledger, now=self.now,
            )
        self.assertEqual(code3_held, 3)
        self.assertEqual(out_buf.getvalue().strip(), 'CLAIM_HELD')

    def test_parser_options(self):
        """add_board_check_parser configures --claim, --holder, and --task options."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest='command')
        board_check.add_board_check_parser(sub)
        parsed = parser.parse_args([
            'board-check', '--claim', 'row-1', '--holder', 'pm-1', '--task', 'a' * 32,
        ])
        self.assertEqual(parsed.claim, 'row-1')
        self.assertEqual(parsed.holder, 'pm-1')
        self.assertEqual(parsed.task, 'a' * 32)
