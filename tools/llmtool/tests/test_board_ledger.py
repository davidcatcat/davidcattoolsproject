"""Unit tests for BoardLedger (#763).

Covers reading, writing, atomic updates, lock semantics, permission checks,
and validation of the cooperative process-level claim ledger.
"""

import fcntl
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import board_check  # noqa: E402


class BoardLedgerTests(unittest.TestCase):
    """Tests for BoardLedger operations and constraints."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ledger_path = self.dir / 'claims.json'
        self.ledger = board_check.BoardLedger(self.ledger_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_reads_empty_list(self):
        """Reading a nonexistent ledger returns an empty list."""
        self.assertFalse(self.ledger_path.exists())
        self.assertEqual(self.ledger.read(), [])

    def test_round_trip_write_and_read_with_mode_0600(self):
        """Writing claims and reading them back preserves data with mode 0600."""
        claims = [
            {
                'holder': 'pm-a',
                'row': 'PR #1',
                'claimed_at': '2026-11-10T00:00:00Z',
                'task_id': None,
                'bound_at': None,
            },
            {
                'holder': 'pm-b',
                'row': 'PR #2',
                'claimed_at': '2026-11-10T01:00:00Z',
                'task_id': '1' * 32,
                'bound_at': '2026-11-10T01:05:00Z',
            },
        ]
        self.ledger.write(claims)
        self.assertEqual(self.ledger.read(), claims)

        st = os.stat(self.ledger_path)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)

    def test_read_symlink_raises_ledger_unreadable(self):
        """A symlinked ledger file raises LedgerUnreadable."""
        target = self.dir / 'real_claims.json'
        target.write_text(json.dumps({'schema_version': 1, 'claims': []}), encoding='utf-8')
        os.chmod(target, 0o600)
        self.ledger_path.symlink_to(target)

        with self.assertRaises(board_check.LedgerUnreadable):
            self.ledger.read()

    def test_read_mode_0644_raises_ledger_unreadable(self):
        """A ledger file with mode 0644 raises LedgerUnreadable."""
        self.ledger_path.write_text(
            json.dumps({'schema_version': 1, 'claims': []}), encoding='utf-8')
        os.chmod(self.ledger_path, 0o644)

        with self.assertRaises(board_check.LedgerUnreadable):
            self.ledger.read()

    def test_read_oversize_raises_ledger_unreadable(self):
        """A ledger file exceeding MAX_LEDGER_BYTES raises LedgerUnreadable."""
        oversize_data = b' ' * (board_check.MAX_LEDGER_BYTES + 1)
        self.ledger_path.write_bytes(oversize_data)
        os.chmod(self.ledger_path, 0o600)

        with self.assertRaises(board_check.LedgerUnreadable):
            self.ledger.read()

    def test_read_invalid_json_raises_ledger_unreadable(self):
        """Invalid JSON content raises LedgerUnreadable."""
        self.ledger_path.write_bytes(b'{not valid json}')
        os.chmod(self.ledger_path, 0o600)

        with self.assertRaises(board_check.LedgerUnreadable):
            self.ledger.read()

    def test_read_schema_version_mismatch_raises_ledger_unreadable(self):
        """A schema_version other than 1 raises LedgerUnreadable."""
        self.ledger_path.write_text(
            json.dumps({'schema_version': 2, 'claims': []}), encoding='utf-8')
        os.chmod(self.ledger_path, 0o600)

        with self.assertRaises(board_check.LedgerUnreadable):
            self.ledger.read()

    def test_read_bad_holder_raises_ledger_unreadable(self):
        """A claim with an invalid holder raises LedgerUnreadable."""
        claim = {
            'holder': 'invalid holder space',
            'row': 'PR #1',
            'claimed_at': '2026-11-10T00:00:00Z',
            'task_id': None,
            'bound_at': None,
        }
        self.ledger_path.write_text(
            json.dumps({'schema_version': 1, 'claims': [claim]}), encoding='utf-8')
        os.chmod(self.ledger_path, 0o600)

        with self.assertRaises(board_check.LedgerUnreadable):
            self.ledger.read()

    def test_read_task_id_present_without_bound_at_raises_ledger_unreadable(self):
        """A claim with task_id present but bound_at missing raises LedgerUnreadable."""
        claim = {
            'holder': 'pm-a',
            'row': 'PR #1',
            'claimed_at': '2026-11-10T00:00:00Z',
            'task_id': '1' * 32,
            'bound_at': None,
        }
        self.ledger_path.write_text(
            json.dumps({'schema_version': 1, 'claims': [claim]}), encoding='utf-8')
        os.chmod(self.ledger_path, 0o600)

        with self.assertRaises(board_check.LedgerUnreadable):
            self.ledger.read()

    def test_write_over_max_claims_raises_claim_refused(self):
        """Attempting to write more than MAX_LEDGER_CLAIMS raises ClaimRefused LEDGER_FULL."""
        claims = [
            {
                'holder': f'pm-{i}',
                'row': f'PR #{i}',
                'claimed_at': '2026-11-10T00:00:00Z',
                'task_id': None,
                'bound_at': None,
            }
            for i in range(board_check.MAX_LEDGER_CLAIMS + 1)
        ]
        with self.assertRaises(board_check.ClaimRefused) as cm:
            self.ledger.write(claims)
        self.assertEqual(cm.exception.code, 'LEDGER_FULL')

    def test_locked_raises_claim_refused_busy_when_held(self):
        """A locked ledger raises ClaimRefused LEDGER_BUSY after lock timeout."""
        child_code = (
            "import fcntl, sys, time\n"
            "fd = open(sys.argv[1], 'w')\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "sys.stdout.write('READY\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(2)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, '-c', child_code, str(self.ledger.lock_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready = proc.stdout.readline()
            self.assertEqual(ready.strip(), 'READY')

            with mock.patch.object(board_check, 'LEDGER_LOCK_TIMEOUT_SECONDS', 0.2):
                with self.assertRaises(board_check.ClaimRefused) as cm:
                    with self.ledger.locked():
                        pass
                self.assertEqual(cm.exception.code, 'LEDGER_BUSY')
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_group_or_world_writable_parent_raises_claim_refused_unavailable(self):
        """A group or world-writable directory raises ClaimRefused LEDGER_UNAVAILABLE."""
        sub = self.dir / 'insecure'
        sub.mkdir(mode=0o777)
        os.chmod(sub, 0o777)
        insecure_ledger = board_check.BoardLedger(sub / 'claims.json')

        with self.assertRaises(board_check.ClaimRefused) as cm:
            with insecure_ledger.locked():
                pass
        self.assertEqual(cm.exception.code, 'LEDGER_UNAVAILABLE')

    def test_default_ledger_path(self):
        """default_ledger_path returns expected standard location."""
        expected = Path.home() / '.local' / 'state' / 'llmtool' / 'pm-claims.json'
        self.assertEqual(board_check.default_ledger_path(), expected)

    def test_valid_claim_helper(self):
        """_valid_claim correctly validates claims and rejects malformed fields."""
        valid = {
            'holder': 'pm-a',
            'row': 'PR #1',
            'claimed_at': '2026-11-10T00:00:00Z',
            'task_id': None,
            'bound_at': None,
        }
        self.assertTrue(board_check._valid_claim(valid))

        valid_bound = dict(valid, task_id='a' * 32, bound_at='2026-11-10T00:05:00Z')
        self.assertTrue(board_check._valid_claim(valid_bound))

        # Missing field
        missing = dict(valid)
        del missing['holder']
        self.assertFalse(board_check._valid_claim(missing))

        # Extra field
        extra = dict(valid, extra_field='val')
        self.assertFalse(board_check._valid_claim(extra))

        # Bound without task_id
        bound_no_task = dict(valid, bound_at='2026-11-10T00:05:00Z')
        self.assertFalse(board_check._valid_claim(bound_no_task))

        # Invalid task_id shape
        bad_task = dict(valid, task_id='not-hex', bound_at='2026-11-10T00:05:00Z')
        self.assertFalse(board_check._valid_claim(bad_task))


if __name__ == '__main__':
    unittest.main()
