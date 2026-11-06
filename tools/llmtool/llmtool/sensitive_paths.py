"""Match repository paths against the installed llmtool sensitive-path list.

The list is read from the installed copy of llmtool, never from a PR head. This
is an accident guard for same-user AIs, not a security boundary.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

MAX_LIST_BYTES = 65536
INVALID_FILE_ENTRY = '<invalid-file-entry>'
SENSITIVE_LIST_UNREADABLE = '<sensitive-list-unreadable>'
_FIELDS = frozenset(('schema_version', 'exact', 'prefixes', 'globs'))


def _list_path() -> Path:
    return Path(__file__).resolve().parents[1] / 'sensitive-paths.json'


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _valid_entry(entry):
    return (type(entry) is str and bool(entry) and not entry.startswith('/') and
            '\\' not in entry and '..' not in entry.split('/'))


def _read_list():
    try:
        with _list_path().open('rb') as stream:
            raw = stream.read(MAX_LIST_BYTES + 1)
        if len(raw) > MAX_LIST_BYTES:
            return None
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (type(value) is not dict or set(value) != _FIELDS or
                type(value.get('schema_version')) is not int or
                value['schema_version'] != 1):
            return None
        for field in ('exact', 'prefixes', 'globs'):
            entries = value[field]
            if type(entries) is not list or any(not _valid_entry(item) for item in entries):
                return None
        if not value['exact'] or any(not item.endswith('/') for item in value['prefixes']):
            return None
        return value
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        return None


def _matches(path, rules):
    if type(path) is not str or not path:
        return True
    return (path in rules['exact'] or
            any(path.startswith(prefix) for prefix in rules['prefixes']) or
            any(fnmatch.fnmatchcase(path, pattern) for pattern in rules['globs']))


def is_sensitive(path) -> bool:
    """Return whether path matches the current list; invalid input fails closed."""
    rules = _read_list()
    if rules is None or type(path) is not str or not path:
        return True
    return _matches(path, rules)


def sensitive_matches(files) -> tuple[str, ...]:
    """Return sorted, unique sensitive paths from GitHub pull-file entries."""
    rules = _read_list()
    if rules is None:
        return (SENSITIVE_LIST_UNREADABLE,)
    if type(files) is not list:
        return (INVALID_FILE_ENTRY,)
    matches = set()
    for entry in files:
        if type(entry) is str:
            paths = (entry,)
        elif type(entry) is dict:
            filename = entry.get('filename')
            previous_present = 'previous_filename' in entry
            previous = entry.get('previous_filename')
            if (type(filename) is not str or not filename or
                    (previous_present and (type(previous) is not str or not previous))):
                return (INVALID_FILE_ENTRY,)
            paths = (filename, previous) if previous_present else (filename,)
        else:
            return (INVALID_FILE_ENTRY,)
        matches.update(path for path in paths if _matches(path, rules))
    return tuple(sorted(matches))
