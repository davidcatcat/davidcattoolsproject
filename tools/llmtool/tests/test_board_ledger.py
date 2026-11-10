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
        expected = Path.home() / '.local' / 'state' / 'llmtool' / 'board-ledger.json'
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

    def test_constants_verbatim(self):
        """Constants match the specification from commit 91d03289."""
        self.assertEqual(board_check.LEDGER_SCHEMA_VERSION, 1)
        self.assertEqual(board_check.MAX_LEDGER_BYTES, 256 * 1024)
        self.assertEqual(board_check.MAX_LEDGER_CLAIMS, 1024)
        self.assertEqual(board_check.LEDGER_LOCK_TIMEOUT_SECONDS, 10)
        self.assertEqual(board_check.CLAIM_REFUSED_EXIT, 3)

    def test_seconds_helper(self):
        """_seconds helper formats datetimes and parses ISO UTC strings."""
        dt = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(board_check._seconds(dt), '2026-11-10T12:00:00Z')
        self.assertEqual(board_check._seconds('2026-11-10T12:00:00Z'), dt)
        self.assertIsNone(board_check._seconds('not-a-date'))
        self.assertIsNone(board_check._seconds(12345))

    def test_write_invalid_claim_raises_claim_refused_invalid(self):
        """Writing an invalid claim raises ClaimRefused LEDGER_CLAIM_INVALID."""
        invalid_claims = [{'holder': 'bad holder!'}]
        with self.assertRaises(board_check.ClaimRefused) as cm:
            self.ledger.write(invalid_claims)
        self.assertEqual(cm.exception.code, 'LEDGER_CLAIM_INVALID')

    def test_read_claims_over_limit_raises_ledger_unreadable(self):
        """A ledger file with more than MAX_LEDGER_CLAIMS raises LedgerUnreadable."""
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
        self.ledger_path.write_text(
            json.dumps({'schema_version': 1, 'claims': claims}), encoding='utf-8')
        os.chmod(self.ledger_path, 0o600)
        with self.assertRaises(board_check.LedgerUnreadable):
            self.ledger.read()

    def test_holder_pattern_accepts_subpm_and_host_formats(self):
        """Holders like subpm:claude-redo:r1218a and pm@host are valid."""
        for holder in ('subpm:claude-redo:r1218a', 'pm@host', 'pm-a', 'worker.1'):
            claim = {
                'holder': holder,
                'row': 'PR #1',
                'claimed_at': '2026-11-10T00:00:00Z',
                'task_id': None,
                'bound_at': None,
            }
            self.assertTrue(board_check._valid_claim(claim))

        for bad in (':invalid', '@host', 'has space', ''):
            claim = {
                'holder': bad,
                'row': 'PR #1',
                'claimed_at': '2026-11-10T00:00:00Z',
                'task_id': None,
                'bound_at': None,
            }
            self.assertFalse(board_check._valid_claim(claim))

    def test_read_different_uid_raises_ledger_unreadable(self):
        """A ledger file with st_uid not matching current user raises LedgerUnreadable."""
        self.ledger.write([
            {
                'holder': 'pm-a',
                'row': 'PR #1',
                'claimed_at': '2026-11-10T00:00:00Z',
                'task_id': None,
                'bound_at': None,
            }
        ])
        real_fstat = os.fstat

        def mock_fstat(fd):
            st = real_fstat(fd)
            return os.stat_result((
                st.st_mode,
                st.st_ino,
                st.st_dev,
                st.st_nlink,
                os.getuid() + 1,
                st.st_gid,
                st.st_size,
                int(st.st_atime),
                int(st.st_mtime),
                int(st.st_ctime),
            ))

        with mock.patch('os.fstat', side_effect=mock_fstat):
            with self.assertRaises(board_check.LedgerUnreadable):
                self.ledger.read()

    def test_read_multiple_hard_links_raises_ledger_unreadable(self):
        """A ledger file with st_nlink != 1 raises LedgerUnreadable."""
        self.ledger.write([
            {
                'holder': 'pm-a',
                'row': 'PR #1',
                'claimed_at': '2026-11-10T00:00:00Z',
                'task_id': None,
                'bound_at': None,
            }
        ])
        hard_link = self.dir / 'hardlink.json'
        os.link(self.ledger_path, hard_link)
        with self.assertRaises(board_check.LedgerUnreadable):
            self.ledger.read()

    def test_locked_lock_file_symlink_raises_claim_refused_unavailable(self):
        """A symlink lock file raises ClaimRefused LEDGER_UNAVAILABLE."""
        target = self.dir / 'real_lock.txt'
        target.write_text('', encoding='utf-8')
        self.ledger.lock_path.symlink_to(target)

        with self.assertRaises(board_check.ClaimRefused) as cm:
            with self.ledger.locked():
                pass
        self.assertEqual(cm.exception.code, 'LEDGER_UNAVAILABLE')

    def test_locked_lock_file_different_uid_raises_claim_refused_unavailable(self):
        """A lock file with st_uid not matching current user raises ClaimRefused."""
        real_fstat = os.fstat

        def mock_fstat(fd):
            st = real_fstat(fd)
            return os.stat_result((
                st.st_mode,
                st.st_ino,
                st.st_dev,
                st.st_nlink,
                os.getuid() + 1,
                st.st_gid,
                st.st_size,
                int(st.st_atime),
                int(st.st_mtime),
                int(st.st_ctime),
            ))

        with mock.patch('os.fstat', side_effect=mock_fstat):
            with self.assertRaises(board_check.ClaimRefused) as cm:
                with self.ledger.locked():
                    pass
            self.assertEqual(cm.exception.code, 'LEDGER_UNAVAILABLE')

    def test_parent_folder_symlink_raises_claim_refused_unavailable(self):
        """A parent folder that is a symlink raises ClaimRefused LEDGER_UNAVAILABLE."""
        real_dir = self.dir / 'real_dir'
        real_dir.mkdir(mode=0o700)
        symlink_dir = self.dir / 'symlink_dir'
        symlink_dir.symlink_to(real_dir, target_is_directory=True)

        symlink_ledger = board_check.BoardLedger(symlink_dir / 'claims.json')
        with self.assertRaises(board_check.ClaimRefused) as cm:
            with symlink_ledger.locked():
                pass
        self.assertEqual(cm.exception.code, 'LEDGER_UNAVAILABLE')

        with self.assertRaises(board_check.ClaimRefused) as cm:
            symlink_ledger.write([
                {
                    'holder': 'pm-a',
                    'row': 'PR #1',
                    'claimed_at': '2026-11-10T00:00:00Z',
                    'task_id': None,
                    'bound_at': None,
                }
            ])
        self.assertEqual(cm.exception.code, 'LEDGER_UNAVAILABLE')

    def test_parent_folder_different_uid_raises_claim_refused_unavailable(self):
        """A parent folder not owned by current user raises ClaimRefused LEDGER_UNAVAILABLE."""
        real_lstat = os.lstat

        def mock_lstat(path):
            st = real_lstat(path)
            if str(path) == str(self.dir):
                return os.stat_result((
                    st.st_mode,
                    st.st_ino,
                    st.st_dev,
                    st.st_nlink,
                    os.getuid() + 1,
                    st.st_gid,
                    st.st_size,
                    int(st.st_atime),
                    int(st.st_mtime),
                    int(st.st_ctime),
                ))
            return st

        with mock.patch('os.lstat', side_effect=mock_lstat):
            with self.assertRaises(board_check.ClaimRefused) as cm:
                with self.ledger.locked():
                    pass
            self.assertEqual(cm.exception.code, 'LEDGER_UNAVAILABLE')

            with self.assertRaises(board_check.ClaimRefused) as cm:
                self.ledger.write([
                    {
                        'holder': 'pm-a',
                        'row': 'PR #1',
                        'claimed_at': '2026-11-10T00:00:00Z',
                        'task_id': None,
                        'bound_at': None,
                    }
                ])
            self.assertEqual(cm.exception.code, 'LEDGER_UNAVAILABLE')

    def test_write_short_write_raises_claim_refused_and_preserves_old_ledger(self):
        """A short write raises ClaimRefused LEDGER_WRITE_FAILED and preserves old data."""
        old_claims = [
            {
                'holder': 'pm-a',
                'row': 'PR #1',
                'claimed_at': '2026-11-10T00:00:00Z',
                'task_id': None,
                'bound_at': None,
            }
        ]
        self.ledger.write(old_claims)

        new_claims = [
            {
                'holder': 'pm-b',
                'row': 'PR #2',
                'claimed_at': '2026-11-10T01:00:00Z',
                'task_id': None,
                'bound_at': None,
            }
        ]

        call_count = 0

        def mock_write(fd, data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return min(5, len(data))
            return 0

        with mock.patch('os.write', side_effect=mock_write):
            with self.assertRaises(board_check.ClaimRefused) as cm:
                self.ledger.write(new_claims)
            self.assertEqual(cm.exception.code, 'LEDGER_WRITE_FAILED')

        self.assertEqual(self.ledger.read(), old_claims)
        temp_files = list(self.dir.glob(f'{self.ledger_path.name}.tmp.*'))
        self.assertEqual(temp_files, [])

    def test_write_replace_oserror_raises_claim_refused_and_leaves_no_temp_file(self):
        """os.replace raising OSError gives LEDGER_WRITE_FAILED and leaves no temp file."""
        claims = [
            {
                'holder': 'pm-a',
                'row': 'PR #1',
                'claimed_at': '2026-11-10T00:00:00Z',
                'task_id': None,
                'bound_at': None,
            }
        ]
        with mock.patch('os.replace', side_effect=OSError('replace failed')):
            with self.assertRaises(board_check.ClaimRefused) as cm:
                self.ledger.write(claims)
            self.assertEqual(cm.exception.code, 'LEDGER_WRITE_FAILED')

        temp_files = list(self.dir.glob(f'{self.ledger_path.name}.tmp.*'))
        self.assertEqual(temp_files, [])

    def test_write_fsync_called_on_file_and_directory(self):
        """os.fsync is called on both the file and the parent folder."""
        claims = [
            {
                'holder': 'pm-a',
                'row': 'PR #1',
                'claimed_at': '2026-11-10T00:00:00Z',
                'task_id': None,
                'bound_at': None,
            }
        ]
        with mock.patch('os.fsync', wraps=os.fsync) as mock_fsync:
            self.ledger.write(claims)
            self.assertEqual(mock_fsync.call_count, 2)

    def test_write_fsync_oserror_raises_claim_refused(self):
        """os.fsync raising OSError gives LEDGER_WRITE_FAILED."""
        claims = [
            {
                'holder': 'pm-a',
                'row': 'PR #1',
                'claimed_at': '2026-11-10T00:00:00Z',
                'task_id': None,
                'bound_at': None,
            }
        ]
        with mock.patch('os.fsync', side_effect=OSError('fsync failed')):
            with self.assertRaises(board_check.ClaimRefused) as cm:
                self.ledger.write(claims)
            self.assertEqual(cm.exception.code, 'LEDGER_WRITE_FAILED')


if __name__ == '__main__':
    unittest.main()
