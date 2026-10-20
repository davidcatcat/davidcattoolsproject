"""One-use, read-only adapter for the active OpenCode v2.0.32 credential.

OpenCode v2.0.32 has no active-only credential API. Its credential list API
returns every credential value, so this adapter intentionally avoids that API
and ``auth export``. It reads only the selected row's ``value`` column from the
pinned v2.0.32 SQLite schema. All discovery, metadata and SQL errors fail
closed without embedding paths, SQL values or underlying exceptions.

The default path assumes the official ``latest`` release channel
(``~/.local/share/opencode/opencode.db``). ``OPENCODE_DB`` and
``XDG_DATA_HOME`` follow the pinned OpenCode path resolver. Other release
channels must set ``OPENCODE_DB`` explicitly; channel guessing is unsafe.
"""
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import time


PINNED_OPENCODE_VERSION = '2.0.32'
INTEGRATION_ID = 'opencode'
DEFAULT_DB_NAME = 'opencode.db'
_ALLOWED_VALUE_TYPES = frozenset(('key', 'oauth'))


class NativeCredentialError(Exception):
    """Sanitized credential-source failure; never includes a value or path."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _error(code):
    raise NativeCredentialError(code) from None


def _check_deadline(deadline):
    if time.monotonic() >= deadline:
        _error('CREDENTIAL_SOURCE_TIMEOUT')


def database_path(environ=None, home=None):
    """Resolve only the supported v2.0.32 latest-channel/OPENCODE_DB paths."""
    env = os.environ if environ is None else environ
    home_path = Path.home() if home is None else Path(home)
    data = Path(env.get('XDG_DATA_HOME') or home_path / '.local' / 'share') / 'opencode'
    override = env.get('OPENCODE_DB')
    if override:
        if override == ':memory:':
            _error('CREDENTIAL_SOURCE_UNKNOWN')
        filename = Path(override)
        return filename if filename.is_absolute() else data / filename
    return data / DEFAULT_DB_NAME


def _physical_file(path):
    """Validate a private, physical database and return its stable identity."""
    try:
        original = Path(os.path.abspath(os.fspath(path)))
        parent = original.parent
        if parent.resolve(strict=True) != parent:
            _error('CREDENTIAL_SOURCE_UNKNOWN')
        current = parent
        while True:
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                _error('CREDENTIAL_SOURCE_UNKNOWN')
            if current.parent == current:
                break
            current = current.parent
        info = original.lstat()
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or
                info.st_uid != os.getuid() or info.st_nlink != 1 or
                stat.S_IMODE(info.st_mode) & 0o077):
            _error('CREDENTIAL_SOURCE_UNKNOWN')
        return original, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    except NativeCredentialError:
        raise
    except Exception:
        _error('CREDENTIAL_SOURCE_UNKNOWN')


def _wal_mode_and_sidecars(path):
    """Read SQLite's non-secret header; refuse WAL opens that could create files."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            header = os.read(fd, 100)
        finally:
            os.close(fd)
        if len(header) < 20 or header[:16] != b'SQLite format 3\x00':
            _error('CREDENTIAL_SOURCE_UNKNOWN')
        read_version, write_version = header[18], header[19]
        if read_version not in (1, 2) or write_version not in (1, 2):
            _error('CREDENTIAL_SOURCE_UNKNOWN')
        wal = Path(str(path) + '-wal')
        shm = Path(str(path) + '-shm')
        journal = Path(str(path) + '-journal')
        present = (wal.exists(), shm.exists(), journal.exists())
        if read_version == 2 or write_version == 2:
            if not (present[0] and present[1]) or present[2]:
                _error('CREDENTIAL_WAL_UNAVAILABLE')
        elif any(present):
            # A rollback journal can need recovery writes; WAL files do not belong
            # to a rollback-mode database. Neither case is safe for this reader.
            _error('CREDENTIAL_SOURCE_UNKNOWN')
        for sidecar, exists in zip((wal, shm), present[:2]):
            if not exists:
                continue
            info = sidecar.lstat()
            if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or
                    info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
                _error('CREDENTIAL_SOURCE_UNKNOWN')
        return present
    except NativeCredentialError:
        raise
    except Exception:
        _error('CREDENTIAL_SOURCE_UNKNOWN')


def read_active_credential(path, *, timeout_seconds=1.0, connector=None):
    """Read one OpenCode credential value under a bounded read-only transaction.

    The metadata-only count permits either one explicit active row, or a single
    legacy row (which OpenCode v2.0.32 selects when it is the only row). Multiple
    rows with no active marker and multiple active rows are ambiguous and refused.
    """
    if (type(timeout_seconds) not in (int, float) or timeout_seconds <= 0 or
            timeout_seconds > 5 or not math.isfinite(timeout_seconds)):
        _error('CREDENTIAL_SOURCE_UNKNOWN')
    db = None
    value = None
    code = None
    deadline = None
    try:
        physical, before = _physical_file(path)
        before_sidecars = _wal_mode_and_sidecars(physical)
        deadline = time.monotonic() + timeout_seconds
        connect = sqlite3.connect if connector is None else connector
        uri = physical.as_uri() + '?mode=ro'
        db = connect(uri, uri=True, timeout=timeout_seconds)
        _check_deadline(deadline)
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 500)
        db.execute('BEGIN')

        columns = {row[1] for row in db.execute('PRAGMA table_info(credential)').fetchall()}
        _check_deadline(deadline)
        if not {'id', 'integration_id', 'value', 'active', 'time_created'} <= columns:
            _error('CREDENTIAL_SOURCE_UNKNOWN')
        row = db.execute(
            'SELECT COUNT(*), COALESCE(SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END), 0) '
            'FROM credential WHERE integration_id = ?', (INTEGRATION_ID,)
        ).fetchone()
        _check_deadline(deadline)
        if (row is None or type(row[0]) is not int or type(row[1]) is not int or
                row[0] < 1):
            _error('CREDENTIAL_MISSING')
        total, active = row
        if active == 1:
            rows = db.execute(
                'SELECT value FROM credential WHERE integration_id = ? AND active = 1 LIMIT 2',
                (INTEGRATION_ID,)
            ).fetchmany(2)
        elif active == 0 and total == 1:
            rows = db.execute(
                'SELECT value FROM credential WHERE integration_id = ? LIMIT 2',
                (INTEGRATION_ID,)
            ).fetchmany(2)
        else:
            _error('CREDENTIAL_AMBIGUOUS')
        if len(rows) != 1 or type(rows[0][0]) is not str:
            _error('CREDENTIAL_SOURCE_UNKNOWN')
        value = json.loads(rows[0][0])
        if type(value) is not dict or value.get('type') not in _ALLOWED_VALUE_TYPES:
            value = None
            _error('CREDENTIAL_SOURCE_UNKNOWN')
        if time.monotonic() >= deadline:
            value = None
            _error('CREDENTIAL_SOURCE_TIMEOUT')
        after, _ = _physical_file(physical)
        after_sidecars = _wal_mode_and_sidecars(after)
        info = after.lstat()
        identity_after = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if after != physical or identity_after != before or after_sidecars != before_sidecars:
            value = None
            _error('CREDENTIAL_SOURCE_UNKNOWN')
    except NativeCredentialError as exc:
        code = exc.code
        value = None
    except sqlite3.OperationalError as exc:
        value = None
        code = 'CREDENTIAL_SOURCE_TIMEOUT' if 'locked' in str(exc).lower() or 'busy' in str(exc).lower() else 'CREDENTIAL_SOURCE_UNKNOWN'
    except Exception:
        value = None
        code = 'CREDENTIAL_SOURCE_UNKNOWN'
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                value = None
                code = 'CREDENTIAL_CLEANUP_UNKNOWN'
        if deadline is not None and time.monotonic() >= deadline:
            value = None
            if code is None:
                code = 'CREDENTIAL_SOURCE_TIMEOUT'
    if code is not None:
        _error(code)
    return value


class ActiveOpenCodeCredential:
    """Lazy single-use source consumed only after Host admission and readiness."""

    def __init__(self, path=None, *, timeout_seconds=1.0, connector=None):
        self._path = database_path() if path is None else Path(path)
        self._timeout_seconds = timeout_seconds
        self._connector = connector
        self._used = False

    def take(self, version):
        if self._used:
            _error('CREDENTIAL_ALREADY_USED')
        self._used = True
        if version != PINNED_OPENCODE_VERSION:
            _error('CREDENTIAL_SOURCE_UNKNOWN')
        value = read_active_credential(self._path, timeout_seconds=self._timeout_seconds,
                                       connector=self._connector)
        return value
