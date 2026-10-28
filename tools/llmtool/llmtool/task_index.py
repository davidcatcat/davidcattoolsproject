"""Best-effort metadata index for read-only Task status listings."""

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path


_INDEX_NAME = 'task-status-index.json'
_MAX_INDEX_BYTES = 4 * 1024 * 1024
_SCHEMA_VERSION = 1


def validator_fingerprint():
    """Hash the names and contents of the Python validators in this package."""
    package = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for source in sorted(package.glob('*.py'), key=lambda item: item.name):
        name = source.name.encode('utf-8')
        contents = source.read_bytes()
        digest.update(len(name).to_bytes(4, 'big'))
        digest.update(name)
        digest.update(len(contents).to_bytes(8, 'big'))
        digest.update(contents)
    return digest.hexdigest()


def _stat_key(path):
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise OSError('task record is not a regular file')
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _valid_entry(task_id, value):
    if type(value) is not dict or set(value) != {
            'stat', 'readable', 'created_at', 'pm_session_id'}:
        return False
    key = value['stat']
    if (type(key) is not list or len(key) != 5 or
            any(type(part) is not int for part in key) or
            type(value['readable']) is not bool):
        return False
    if value['readable']:
        return (type(value['created_at']) is str and
                (value['pm_session_id'] is None or
                 type(value['pm_session_id']) is str))
    return value['created_at'] is None and value['pm_session_id'] is None


class TaskStatusIndex:
    """A disposable index; every failure falls back to TaskStore.get."""

    def __init__(self, store):
        self.store = store
        self.path = None
        self.fingerprint = None
        self.entries = {}
        self.enabled = False
        self.changed = False
        try:
            root = Path(store.root)
            if not root.is_absolute():
                return
            root_info = os.stat(root, follow_symlinks=False)
            parent_info = os.stat(root.parent, follow_symlinks=False)
            if (not stat.S_ISDIR(root_info.st_mode) or
                    root_info.st_uid != os.getuid() or
                    stat.S_IMODE(root_info.st_mode) != 0o700 or
                    not stat.S_ISDIR(parent_info.st_mode) or
                    parent_info.st_uid != os.getuid() or
                    parent_info.st_mode & 0o022):
                return
            self.path = root.parent / _INDEX_NAME
            self.fingerprint = validator_fingerprint()
            try:
                self.entries = self._read()
            except Exception:
                self.entries = {}
            self.enabled = True
        except Exception:
            self.path = None
            self.entries = {}

    def _read(self):
        try:
            info = os.stat(self.path, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) != 0o600 or
                    info.st_size > _MAX_INDEX_BYTES):
                return {}
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
            descriptor = os.open(self.path, flags)
        except Exception:
            return {}
        try:
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid() or
                    stat.S_IMODE(opened.st_mode) != 0o600 or
                    opened.st_size > _MAX_INDEX_BYTES):
                return {}
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                raw = stream.read(_MAX_INDEX_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(raw) > _MAX_INDEX_BYTES:
            return {}
        data = json.loads(raw.decode('utf-8'))
        if (type(data) is not dict or set(data) !=
                {'schema_version', 'fingerprint', 'entries'} or
                type(data['schema_version']) is not int or
                data['schema_version'] != _SCHEMA_VERSION or
                data['fingerprint'] != self.fingerprint or
                type(data['entries']) is not dict):
            return {}
        entries = data['entries']
        if any(type(task_id) is not str or not _valid_entry(task_id, value)
               for task_id, value in entries.items()):
            return {}
        return entries

    def _save(self):
        if not self.enabled or self.path is None:
            return
        payload = json.dumps({
            'schema_version': _SCHEMA_VERSION,
            'fingerprint': self.fingerprint,
            'entries': self.entries,
        }, sort_keys=True, separators=(',', ':')).encode('utf-8')
        if len(payload) > _MAX_INDEX_BYTES:
            return
        temp_path = self.path.parent / ('.task-status-index-' + uuid.uuid4().hex)
        descriptor = None
        try:
            descriptor = os.open(
                temp_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
        except Exception:
            pass
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    def flush(self):
        """Persist refreshed entries once after the status selection."""
        if self.changed:
            self._save()

    def _record_entry(self, task_id, key, record):
        self.entries[task_id] = {
            'stat': list(key),
            'readable': True,
            'created_at': record.get('created_at', ''),
            'pm_session_id': record.get('pm_session_id'),
        }
        self.changed = True

    def remember_full_scan(self, task_id, record=None):
        """Refresh one entry after a required uncached read."""
        if not self.enabled:
            return
        try:
            key = _stat_key(self.store.root / (task_id + '.json'))
            if record is None:
                self._unreadable_entry(task_id, key)
            else:
                self._record_entry(task_id, key, record)
        except Exception:
            pass

    def _unreadable_entry(self, task_id, key):
        if key is None:
            self.entries.pop(task_id, None)
        else:
            self.entries[task_id] = {
                'stat': list(key), 'readable': False,
                'created_at': None, 'pm_session_id': None,
            }
        self.changed = True

    def scan(self, task_ids):
        """Return metadata rows and the number of known unreadable records."""
        rows = []
        unreadable = 0
        for task_id in task_ids:
            key = None
            entry = None
            if self.enabled:
                try:
                    key = _stat_key(self.store.root / (task_id + '.json'))
                    candidate = self.entries.get(task_id)
                    if candidate is not None and candidate['stat'] == list(key):
                        entry = candidate
                except Exception:
                    pass
            if entry is not None:
                if entry['readable']:
                    rows.append((task_id, entry['created_at'], entry['pm_session_id']))
                else:
                    unreadable += 1
                continue
            try:
                record = self.store.get(task_id)
            except Exception:
                unreadable += 1
                if self.enabled:
                    self._unreadable_entry(task_id, key)
                continue
            if self.enabled:
                try:
                    after_key = _stat_key(self.store.root / (task_id + '.json'))
                    self._record_entry(task_id, after_key, record)
                except Exception:
                    pass
            rows.append((task_id, record.get('created_at', ''), record.get('pm_session_id')))
        return rows, unreadable

    def reread(self, task_id):
        """Revalidate a printed record and report metadata disagreement."""
        previous = self.entries.get(task_id) if self.enabled else None
        record = self.store.get(task_id)
        changed = (previous is not None and previous['readable'] and
                   (record.get('created_at', '') != previous['created_at'] or
                    record.get('pm_session_id') != previous['pm_session_id']))
        if self.enabled:
            try:
                key = _stat_key(self.store.root / (task_id + '.json'))
                self._record_entry(task_id, key, record)
            except Exception:
                pass
        return record, changed

    def discard(self):
        self.entries = {}
        self.enabled = False

