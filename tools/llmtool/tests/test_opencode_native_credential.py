"""Synthetic-only tests for the pinned OpenCode credential adapter.

These tests use temporary SQLite fixtures and never inspect the user's OpenCode
configuration or credential store.
"""
import contextlib
import io
import json
import os
import sqlite3
import tempfile
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))
from llmtool import opencode_native_credential as credential


class NativeCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        os.chmod(self.root, 0o700)
        self.db_path = self.root / 'synthetic-opencode.db'

    def create_db(self, rows=()):
        db = sqlite3.connect(self.db_path)
        db.execute('''CREATE TABLE credential (
            id TEXT PRIMARY KEY,
            integration_id TEXT NOT NULL,
            value TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 0,
            time_created INTEGER NOT NULL DEFAULT 0
        )''')
        db.execute('CREATE UNIQUE INDEX credential_active ON credential(integration_id) '
                   'WHERE active = 1')
        db.executemany('INSERT INTO credential '
                       '(id, integration_id, value, active, time_created) VALUES (?, ?, ?, ?, ?)',
                       rows)
        db.commit()
        db.close()
        os.chmod(self.db_path, 0o600)

    @staticmethod
    def row(value='SYNTHETIC_SECRET_NEVER_REAL', *, active=1, row_id='one', integration='opencode'):
        return (row_id, integration,
                json.dumps({'type': 'key', 'key': value}), active, 1)

    def assert_code(self, expected, function, *args, **kwargs):
        with self.assertRaises(credential.NativeCredentialError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, expected)
        self.assertNotIn('SYNTHETIC_SECRET_NEVER_REAL', str(caught.exception))
        self.assertNotIn(str(self.db_path), str(caught.exception))

    def test_missing_credential_and_unrelated_rows_fail_closed(self):
        self.create_db((self.row(integration='other'),))
        self.assert_code('CREDENTIAL_MISSING', credential.read_active_credential, self.db_path)

    def test_source_open_and_schema_errors_are_sanitized(self):
        self.assert_code('CREDENTIAL_SOURCE_UNKNOWN', credential.read_active_credential,
                         self.db_path)
        self.db_path.write_bytes(b'not sqlite')
        os.chmod(self.db_path, 0o600)
        self.assert_code('CREDENTIAL_SOURCE_UNKNOWN', credential.read_active_credential,
                         self.db_path)

        self.db_path.unlink()
        db = sqlite3.connect(self.db_path)
        db.execute('CREATE TABLE credential (id TEXT, value TEXT)')
        db.commit()
        db.close()
        os.chmod(self.db_path, 0o600)
        self.assert_code('CREDENTIAL_SOURCE_UNKNOWN', credential.read_active_credential,
                         self.db_path)

    def test_exact_active_row_is_returned_and_multiple_rows_are_refused(self):
        secret = 'SYNTHETIC_SECRET_NEVER_REAL'
        self.create_db((self.row(secret), self.row('OTHER_SYNTHETIC', active=0,
                                                   row_id='inactive')))
        result = credential.read_active_credential(self.db_path)
        self.assertEqual(result, {'type': 'key', 'key': secret})

        self.db_path.unlink()
        self.create_db((self.row(secret, active=0), self.row('OTHER_SYNTHETIC', active=0,
                                                             row_id='second')))
        self.assert_code('CREDENTIAL_AMBIGUOUS', credential.read_active_credential,
                         self.db_path)

    def test_missing_active_marker_uses_only_single_legacy_row(self):
        self.create_db((self.row(active=0),))
        self.assertEqual(credential.read_active_credential(self.db_path),
                         {'type': 'key', 'key': 'SYNTHETIC_SECRET_NEVER_REAL'})

    def test_timeout_after_open_returns_no_value(self):
        self.create_db((self.row(),))

        def slow_connect(*args, **kwargs):
            time.sleep(0.03)
            return sqlite3.connect(*args, **kwargs)

        self.assert_code('CREDENTIAL_SOURCE_TIMEOUT', credential.read_active_credential,
                         self.db_path, timeout_seconds=0.01, connector=slow_connect)

    def test_one_use_source_reads_only_on_first_supported_take(self):
        self.create_db((self.row(),))
        source = credential.ActiveOpenCodeCredential(self.db_path)
        self.assertEqual(source.take('2.0.32')['key'], 'SYNTHETIC_SECRET_NEVER_REAL')
        self.assert_code('CREDENTIAL_ALREADY_USED', source.take, '2.0.32')

        second = credential.ActiveOpenCodeCredential(self.db_path)
        self.assert_code('CREDENTIAL_SOURCE_UNKNOWN', second.take, 'other-version')

    def test_unexpected_output_and_secret_repr_are_absent(self):
        self.create_db((self.row(),))
        source = credential.ActiveOpenCodeCredential(self.db_path)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = source.take('2.0.32')
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), '')
        self.assertNotIn('SYNTHETIC_SECRET_NEVER_REAL', repr(source))
        self.assertEqual(result['key'], 'SYNTHETIC_SECRET_NEVER_REAL')

    def test_cleanup_failure_discards_value_and_never_exposes_error(self):
        self.create_db((self.row(),))

        class CloseFailure:
            def __init__(self, wrapped):
                self.wrapped = wrapped

            def __getattr__(self, name):
                return getattr(self.wrapped, name)

            def close(self):
                self.wrapped.close()
                raise RuntimeError('SYNTHETIC_SECRET_NEVER_REAL')

        def connector(*args, **kwargs):
            return CloseFailure(sqlite3.connect(*args, **kwargs))

        self.assert_code('CREDENTIAL_CLEANUP_UNKNOWN', credential.read_active_credential,
                         self.db_path, connector=connector)

    def test_unsafe_wal_without_preexisting_sidecars_creates_no_files(self):
        self.create_db((self.row(),))
        db = sqlite3.connect(self.db_path)
        self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        db.close()
        for suffix in ('-wal', '-shm'):
            (Path(str(self.db_path) + suffix)).unlink(missing_ok=True)
        before = sorted(path.name for path in self.root.iterdir())
        self.assert_code('CREDENTIAL_WAL_UNAVAILABLE', credential.read_active_credential,
                         self.db_path)
        after = sorted(path.name for path in self.root.iterdir())
        self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
