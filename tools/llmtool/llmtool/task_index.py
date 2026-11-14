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
_BASE_KEYS = frozenset({'stat', 'readable', 'created_at', 'pm_session_id'})
# Optional 'tui' projection of a readable record: the chat TUI's Task list row and its
# newest events, so the list and events views need no per-record TaskStore.get.
TUI_EVENT_LIMIT = 3     # newest events kept per Task
TUI_TEXT_LIMIT = 200    # characters kept per event text
_TUI_KEYS = frozenset({'updated_at', 'stage', 'name', 'executor', 'prs', 'owner',
                       'terminal', 'events', 'event_count'})


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


def _valid_tui(value):
    """The optional TUI projection: exact keys and JSON types only."""
    if type(value) is not dict or set(value) != _TUI_KEYS:
        return False
    if (not (value['updated_at'] is None or type(value['updated_at']) is str) or
            type(value['stage']) is not str or type(value['name']) is not str or
            not (value['executor'] is None or type(value['executor']) is str) or
            type(value['prs']) is not list or
            any(type(number) is not int for number in value['prs']) or
            type(value['owner']) is not bool or type(value['terminal']) is not bool or
            type(value['events']) is not list or
            len(value['events']) > TUI_EVENT_LIMIT or
            type(value['event_count']) is not int or
            value['event_count'] < len(value['events'])):
        return False
    for event in value['events']:
        if (type(event) is not list or len(event) != 4 or
                not (event[0] is None or type(event[0]) is str) or
                type(event[1]) is not str or type(event[2]) is not str or
                len(event[2]) > TUI_TEXT_LIMIT or type(event[3]) is not bool):
            return False
    return True


def _valid_entry(task_id, value):
    if type(value) is not dict or not (set(value) == _BASE_KEYS or
                                       set(value) == _BASE_KEYS | {'tui'}):
        return False
    key = value['stat']
    if (type(key) is not list or len(key) != 5 or
            any(type(part) is not int for part in key) or
            type(value['readable']) is not bool):
        return False
    if value['readable']:
        return (type(value['created_at']) is str and
                (value['pm_session_id'] is None or
                 type(value['pm_session_id']) is str) and
                ('tui' not in value or _valid_tui(value['tui'])))
    return ('tui' not in value and value['created_at'] is None and
            value['pm_session_id'] is None)


def _tui_summary(record):
    """The TUI projection of one record, or None when it cannot be built."""
    try:
        from .chat_tui_status import index_summary
        summary = index_summary(record)
    except Exception:
        return None
    return summary if _valid_tui(summary) else None


def _index_path(store):
    """The index path beside a private TaskStore root, else None. Only stats paths."""
    root = Path(store.root)
    if not root.is_absolute():
        return None
    root_info = os.stat(root, follow_symlinks=False)
    parent_info = os.stat(root.parent, follow_symlinks=False)
    if (not stat.S_ISDIR(root_info.st_mode) or
            root_info.st_uid != os.getuid() or
            stat.S_IMODE(root_info.st_mode) != 0o700 or
            not stat.S_ISDIR(parent_info.st_mode) or
            parent_info.st_uid != os.getuid() or
            parent_info.st_mode & 0o022):
        return None
    return root.parent / _INDEX_NAME


def _load(path, fingerprint):
    """(entries, None) or (None, reason); reason is missing, unreadable or stale."""
    try:
        info = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return None, 'missing'
    except Exception:
        return None, 'unreadable'
    try:
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or
                info.st_size > _MAX_INDEX_BYTES):
            return None, 'unreadable'
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None, 'missing'
    except Exception:
        return None, 'unreadable'
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid() or
                stat.S_IMODE(opened.st_mode) != 0o600 or
                opened.st_size > _MAX_INDEX_BYTES):
            return None, 'unreadable'
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(_MAX_INDEX_BYTES + 1)
    except Exception:
        return None, 'unreadable'
    finally:
        os.close(descriptor)
    if len(raw) > _MAX_INDEX_BYTES:
        return None, 'unreadable'
    try:
        data = json.loads(raw.decode('utf-8'))
    except Exception:
        return None, 'unreadable'
    if (type(data) is not dict or set(data) !=
            {'schema_version', 'fingerprint', 'entries'} or
            type(data['schema_version']) is not int or
            type(data['entries']) is not dict):
        return None, 'unreadable'
    if data['schema_version'] != _SCHEMA_VERSION or data['fingerprint'] != fingerprint:
        return None, 'stale'
    entries = data['entries']
    if any(type(task_id) is not str or not _valid_entry(task_id, value)
           for task_id, value in entries.items()):
        return None, 'unreadable'
    return entries, None


def display_entries(store):
    """Read-only index read for display: (entries, None) or (None, reason).

    reason: unavailable (store root not private or not found), missing, unreadable or
    stale (schema or validator fingerprint differs). Never writes, creates or repairs.
    """
    try:
        path = _index_path(store)
        if path is None:
            return None, 'unavailable'
        return _load(path, validator_fingerprint())
    except Exception:
        return None, 'unavailable'


def record_key(store, task_id):
    """The index stat key of one record file as a list, or None. Only stats the file."""
    try:
        return list(_stat_key(Path(store.root) / (task_id + '.json')))
    except Exception:
        return None


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
            path = _index_path(store)
            if path is None:
                return
            self.path = path
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
        entries, _reason = _load(self.path, self.fingerprint)
        return {} if entries is None else entries

    def _save(self):
        if not self.enabled or self.path is None:
            return
        payload = self._payload(self.entries)
        if len(payload) > _MAX_INDEX_BYTES:
            # The TUI projection is optional: keep the status metadata within the bound.
            payload = self._payload({
                task_id: {key: item for key, item in value.items() if key != 'tui'}
                for task_id, value in self.entries.items()})
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

    def _payload(self, entries):
        return json.dumps({
            'schema_version': _SCHEMA_VERSION,
            'fingerprint': self.fingerprint,
            'entries': entries,
        }, sort_keys=True, separators=(',', ':')).encode('utf-8')

    def flush(self):
        """Persist refreshed entries once after the status selection."""
        if self.changed:
            self._save()

    def _record_entry(self, task_id, key, record):
        entry = {
            'stat': list(key),
            'readable': True,
            'created_at': record.get('created_at', ''),
            'pm_session_id': record.get('pm_session_id'),
        }
        summary = _tui_summary(record)
        if summary is not None:
            entry['tui'] = summary
        self.entries[task_id] = entry
        self.changed = True

    def remember_full_scan(self, task_id):
        """Read one record and cache it only if its metadata stayed stable."""
        path = self.store.root / (task_id + '.json')
        before_key = None
        try:
            before_key = _stat_key(path)
        except Exception:
            if self.enabled:
                self.entries.pop(task_id, None)
                self.changed = True
        try:
            record = self.store.get(task_id)
        except Exception:
            try:
                after_key = _stat_key(path)
            except Exception:
                after_key = None
            if self.enabled:
                if before_key == after_key:
                    self._unreadable_entry(task_id, before_key)
                else:
                    self.entries.pop(task_id, None)
                    self.changed = True
            return None, False
        try:
            after_key = _stat_key(path)
        except Exception:
            after_key = None
        if self.enabled:
            if before_key is not None and before_key == after_key:
                self._record_entry(task_id, after_key, record)
            else:
                self.entries.pop(task_id, None)
                self.changed = True
        return record, True

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
            path = self.store.root / (task_id + '.json')
            key = None
            entry = None
            try:
                key = _stat_key(path)
            except Exception:
                pass
            if self.enabled:
                try:
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
            before_key = key
            try:
                record = self.store.get(task_id)
            except Exception:
                unreadable += 1
                try:
                    after_key = _stat_key(path)
                except Exception:
                    after_key = None
                if self.enabled:
                    if before_key == after_key:
                        self._unreadable_entry(task_id, before_key)
                    else:
                        self.entries.pop(task_id, None)
                        self.changed = True
                continue
            try:
                after_key = _stat_key(path)
            except Exception:
                after_key = None
            if self.enabled:
                if before_key is not None and before_key == after_key:
                    self._record_entry(task_id, after_key, record)
                else:
                    self.entries.pop(task_id, None)
                    self.changed = True
            rows.append((task_id, record.get('created_at', ''), record.get('pm_session_id')))
        return rows, unreadable

    def reread(self, task_id):
        """Revalidate a printed record and report metadata disagreement."""
        previous = self.entries.get(task_id) if self.enabled else None
        path = self.store.root / (task_id + '.json')
        try:
            before_key = _stat_key(path)
        except Exception:
            before_key = None
        try:
            record = self.store.get(task_id)
        except Exception:
            try:
                after_key = _stat_key(path)
            except Exception:
                after_key = None
            if self.enabled and before_key != after_key:
                self.entries.pop(task_id, None)
                self.changed = True
            raise
        try:
            after_key = _stat_key(path)
        except Exception:
            after_key = None
        changed = (previous is not None and previous['readable'] and
                   (record.get('created_at', '') != previous['created_at'] or
                    record.get('pm_session_id') != previous['pm_session_id']))
        if self.enabled:
            if before_key is not None and before_key == after_key:
                self._record_entry(task_id, after_key, record)
            else:
                self.entries.pop(task_id, None)
                self.changed = True
        return record, changed

    def discard(self):
        self.entries = {}
        self.enabled = False

