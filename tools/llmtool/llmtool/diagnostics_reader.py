"""Offline, bounded, read-only diagnostics snapshots; never use writer helpers."""

import errno
import os
import re
import stat
import time
from contextlib import contextmanager
from dataclasses import fields

from . import diagnostics as d
from .diagnostics_settings import resolve_diagnostics_home
from .diagnostics_store import persistence_supported, RESERVATION_MAX_BYTES, SCAN_LIMIT
from .diagnostics_summary import (SUMMARY_MAX_BYTES, MAX_STORED_RUN_BYTES,
                                  parse_run_summary, strict_object)
from .errors import LLMToolError

RUN_SCAN_LIMIT = 1000
ROOT_SCAN_LIMIT = SCAN_LIMIT
ACCOUNT_DEPTH_LIMIT = 32
RUN_FILE_SCAN_LIMIT = 16
RUN_OUTPUT_LIMIT = 100
RUNS_READ_MAX_BYTES = 4 * 1024 * 1024
TRACE_MAX_BYTES = MAX_STORED_RUN_BYTES
READER_OUTPUT_MAX_BYTES = 256 * 1024
HOME_COMPONENT_LIMIT = 128
RUN_FILES = frozenset(('.run.lock', '.reservation', 'trace.partial.jsonl',
                       'trace.jsonl', 'summary.json'))
SUMMARY_FIELDS = ('run_id', 'command', 'mode', 'status', 'started_at', 'ended_at',
                  'duration_ms', 'provider_type', 'final_error_code', 'event_count',
                  'dropped_event_count', 'trace_bytes')
COMPONENTS = frozenset(('cli.ask', 'input', 'config', 'config.auth', 'provider',
                        'output', 'diagnostics', 'provider.openai',
                        'provider.openai-compatible', 'provider.claude-cli',
                        'provider.codex-cli'))
EVENT_FIELDS = frozenset(f.name for f in fields(d.DiagnosticEvent))
ERROR_FIELDS = frozenset(('code', 'message', 'retryable', 'recoverable',
                         'fallback_allowed', 'user_action_required', 'safe_cause'))
CAUSE_FIELDS = frozenset(('http_status', 'process_status', 'category'))
PROVIDER_FIELDS = frozenset(('provider_type', 'profile', 'model', 'duration_ms',
                            'input_tokens', 'output_tokens', 'prompt_tokens',
                            'completion_tokens', 'total_tokens', 'token_usage_available'))


class ReaderError(Exception):
    """Fixed application-owned errors. Never retain a foreign exception."""

    MESSAGES = {
        'ID_INVALID': 'run identifier must be 32 lowercase hexadecimal characters',
        'NOT_FOUND': 'diagnostics run not found',
        'STORE_UNAVAILABLE': 'diagnostics store unavailable; details omitted',
        'STORE_UNSAFE': 'diagnostics store unsafe; details omitted',
        'FORMAT_INVALID': 'diagnostics metadata damaged; details omitted',
        'FORMAT_UNSUPPORTED': 'diagnostics metadata version unsupported',
        'LIMIT_EXCEEDED': 'diagnostics reader limit exceeded',
    }

    def __init__(self, code):
        self.code = code
        super().__init__(self.MESSAGES[code])

    def line(self):
        return 'llmtool: code=' + self.code + ' ' + str(self)


class _AbsentStore(Exception):
    pass


def _private(info, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o7077
            or (not directory and info.st_nlink != 1)):
        raise ReaderError('STORE_UNSAFE')


def _ancestor(info):
    # #137 R26: owned by root or this user; not group/other-writable unless sticky.
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
            or (mode & 0o022 and not mode & stat.S_ISVTX)):
        raise ReaderError('STORE_UNSAFE')


def _identity(info):
    return info.st_dev, info.st_ino


def _snapshot(info):
    return (_identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_uid, info.st_nlink)


def _close_all(fds):
    failure = None
    for fd in reversed(fds):
        try:
            os.close(fd)
        except OSError as exc:
            failure = exc
    if failure is not None:
        raise ReaderError('STORE_UNAVAILABLE') from None


def _check_link(parent, name, fd, directory=False):
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    original = os.fstat(fd)
    if _identity(current) != _identity(original):
        raise ReaderError('STORE_UNAVAILABLE')
    if directory:
        _private(current, True)


def _directory(parent, name, private=True):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ReaderError('STORE_UNSAFE') from None
        raise
    try:
        if private:
            _private(os.fstat(fd), True)
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def _home(path):
    if not path.is_absolute() or '..' in path.parts or len(path.parts) < 2:
        raise ReaderError('STORE_UNSAFE')
    if len(path.parts) > HOME_COMPONENT_LIMIT:
        raise ReaderError('LIMIT_EXCEEDED')
    fds, links = [], []
    try:
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fds.append(fd)
        _ancestor(os.fstat(fd))
        for index, name in enumerate(path.parts[1:], 1):
            try:
                child = _directory(fd, name, private=index == len(path.parts) - 1)
            except FileNotFoundError:
                raise _AbsentStore() from None
            links.append((fd, name, child, index == len(path.parts) - 1))
            fds.append(child)
            fd = child
            if index < len(path.parts) - 1:
                _ancestor(os.fstat(fd))
        yield fd
        for parent, name, child, private in links:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or _identity(current) != _identity(os.fstat(child)):
                raise ReaderError('STORE_UNAVAILABLE')
            if private:
                _private(current, True)
    finally:
        _close_all(fds)


@contextmanager
def _lock(parent, name):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
    except FileNotFoundError:
        raise ReaderError('STORE_UNSAFE') from None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ReaderError('STORE_UNSAFE') from None
        raise
    try:
        _private(os.fstat(fd))
        _check_link(parent, name, fd)
        import fcntl
        # Only the root lock gets the writer's bounded contention policy.
        # Waiting on a run lock while holding root would obstruct finish().
        deadline = time.monotonic() + 2 if name == '.store.lock' else None
        acquired = False
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if deadline is None or time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
        if not acquired:
            yield False
        else:
            try:
                _check_link(parent, name, fd)
                _private(os.fstat(fd))
                yield True
                _check_link(parent, name, fd)
                _private(os.fstat(fd))
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _names(directory, limit):
    names = []
    with os.scandir(directory) as entries:
        for item in entries:
            if len(names) >= limit:
                raise ReaderError('LIMIT_EXCEEDED')
            names.append(item.name)
    return names


def _account_bytes(parent, budget, *, skip=(), depth=0):
    """Stat-only recursive accounting, bounded like the diagnostics writer."""
    if depth > ACCOUNT_DEPTH_LIMIT:
        raise ReaderError('LIMIT_EXCEEDED')
    total = 0
    names = _names(parent, budget[0] + 1)
    budget[0] -= len(names)
    if budget[0] < 0:
        raise ReaderError('LIMIT_EXCEEDED')
    for name in names:
        if name in skip:
            continue
        total += _account_entry(parent, name, budget, depth=depth)
    return total


def _account_entry(parent, name, budget, *, depth=0):
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if stat.S_ISDIR(info.st_mode):
        if depth >= ACCOUNT_DEPTH_LIMIT:
            raise ReaderError('LIMIT_EXCEEDED')
        child = _directory(parent, name, private=False)
        try:
            if _identity(info) != _identity(os.fstat(child)):
                raise ReaderError('STORE_UNAVAILABLE')
            total = _account_bytes(child, budget, depth=depth + 1)
        finally:
            os.close(child)
    elif stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        # st_size for a symlink is its link text length; never follow it.
        total = info.st_size
    else:
        raise ReaderError('LIMIT_EXCEEDED')
    if _snapshot(info) != _snapshot(os.stat(name, dir_fd=parent, follow_symlinks=False)):
        raise ReaderError('STORE_UNAVAILABLE')
    return total


def _read(parent, name, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
    try:
        original = os.fstat(fd)
        _private(original)
        if original.st_size > limit:
            raise ReaderError('LIMIT_EXCEEDED')
        raw = bytearray()
        while len(raw) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > limit:
            raise ReaderError('LIMIT_EXCEEDED')
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (_snapshot(original) != _snapshot(os.fstat(fd))
                or _snapshot(original) != _snapshot(current) or len(raw) != original.st_size):
            raise ReaderError('STORE_UNAVAILABLE')
        return bytes(raw)
    finally:
        os.close(fd)


def _version(value, schema):
    if value.get('schema') != schema or type(value.get('schema')) is not str:
        raise ReaderError('FORMAT_INVALID')
    version = value.get('schema_version')
    if type(version) is not int:
        raise ReaderError('FORMAT_INVALID')
    if version != 1:
        raise ReaderError('FORMAT_UNSUPPORTED')


def _summary(raw, run_id):
    _version(strict_object(raw, SUMMARY_MAX_BYTES), 'llmtool.diagnostics.summary')
    summary = parse_run_summary(raw)
    if summary.run_id != run_id or summary.event_count > d.MAX_RUN_EVENTS:
        raise ReaderError('FORMAT_INVALID')
    return summary


def _summary_projection(summary):
    value = summary.to_dict()
    return {key: value[key] for key in SUMMARY_FIELDS}


def _incomplete(run_id):
    value = dict.fromkeys(SUMMARY_FIELDS)
    value.update(run_id=run_id, status='incomplete')
    return value


def _event(raw, run_id, event_id):
    if len(raw) > d.EVENT_MAX_BYTES:
        raise ReaderError('LIMIT_EXCEEDED')
    value = strict_object(raw, d.EVENT_MAX_BYTES)
    _version(value, d.EVENT_SCHEMA)
    if not EVENT_FIELDS <= set(value):
        raise ReaderError('FORMAT_INVALID')
    selected = {key: value[key] for key in EVENT_FIELDS}
    if type(selected['type']) is not str or type(selected['severity']) is not str:
        raise ReaderError('FORMAT_INVALID')
    severity = d.DiagnosticSeverity(selected['severity'])
    if type(selected['data']) is not dict:
        raise ReaderError('FORMAT_INVALID')
    try:
        kind = d.EventType(selected['type'])
    except ValueError:
        # Unknown v1 event names are bounded identifiers, never rendered or acted on.
        d.safe_diagnostic_text(selected['type'], d.IDENTIFIER_MAX_CHARS)
        if re.fullmatch(r'[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*', selected['type']) is None:
            raise ReaderError('FORMAT_INVALID')
        selected.update(type=d.EventType.RUN_END, severity=severity, data={})
        event = d.DiagnosticEvent(**selected)
        result = event.to_dict()
        result.update(type='unknown', component='unknown', data={})
    else:
        if kind in (d.EventType.ERROR, d.EventType.WARNING):
            allowed = ERROR_FIELDS
        elif kind is d.EventType.PROVIDER_REQUEST:
            allowed = {'provider_type', 'profile', 'model'}
        elif kind is d.EventType.PROVIDER_RESPONSE:
            allowed = PROVIDER_FIELDS
        elif kind is d.EventType.RUN_START:
            allowed = {'command', 'mode'}
        else:
            allowed = {'status', 'duration_ms'}
        data = {key: item for key, item in selected['data'].items() if key in allowed}
        if 'safe_cause' in data:
            if type(data['safe_cause']) is not dict:
                raise ReaderError('FORMAT_INVALID')
            data['safe_cause'] = {k: v for k, v in data['safe_cause'].items() if k in CAUSE_FIELDS}
        selected.update(type=kind, severity=severity, data=data)
        event = d.DiagnosticEvent(**selected)
        result = event.to_dict()
        result['component'] = result['component'] if result['component'] in COMPONENTS else 'unknown'
        # Even syntactically valid free text in a forged document is not display evidence.
        result['data'] = {k: v for k, v in result['data'].items() if k not in ('message', 'model', 'profile')}
    if event.run_id != run_id or event.event_id != event_id:
        raise ReaderError('FORMAT_INVALID')
    return {key: result[key] for key in ('event_id', 'timestamp', 'type', 'component',
                                       'severity', 'span_id', 'parent_span_id', 'data')}


def _events(raw, run_id, completed):
    omitted = bool(raw and not raw.endswith(b'\n'))
    if omitted and completed:
        raise ReaderError('FORMAT_INVALID')
    lines = raw.split(b'\n')
    if len(lines[-1]) > d.EVENT_MAX_BYTES:
        raise ReaderError('LIMIT_EXCEEDED')
    lines.pop()  # Empty final delimiter or incomplete final line: never render it.
    if len(lines) > d.MAX_RUN_EVENTS:
        raise ReaderError('LIMIT_EXCEEDED')
    result = [_event(line, run_id, index) for index, line in enumerate(lines, 1)]
    if result and result[0]['type'] != 'run.start':
        raise ReaderError('FORMAT_INVALID')
    if any(event['type'] == 'run.start' for event in result[1:]):
        raise ReaderError('FORMAT_INVALID')
    if any(event['type'] == 'run.end' for event in result[:-1]):
        raise ReaderError('FORMAT_INVALID')
    return result, omitted


def _metadata_sound(directory, files, run_id):
    """Validate one completed run's bounded summary; never read trace evidence."""
    if 'trace.jsonl' not in files or 'trace.partial.jsonl' in files:
        return False
    try:
        _summary(_read(directory, 'summary.json', SUMMARY_MAX_BYTES), run_id)
    except ReaderError as exc:
        if exc.code in ('FORMAT_INVALID', 'FORMAT_UNSUPPORTED'):
            return False
        raise
    except (ValueError, TypeError, KeyError):
        return False
    return True


def _consistent(summary, raw, events):
    if (not events or events[-1]['type'] != 'run.end'
            or summary.event_count != len(events) or summary.trace_bytes != len(raw)
            or summary.started_at != events[0]['timestamp']
            or summary.ended_at != events[-1]['timestamp']
            or summary.command != events[0]['data'].get('command')
            or summary.mode.value != events[0]['data'].get('mode')
            or summary.status != events[-1]['data'].get('status')
            or summary.duration_ms != events[-1]['data'].get('duration_ms')):
        raise ReaderError('FORMAT_INVALID')


class DiagnosticsReader:
    """Pure construction; readers never enable persistence or resolve credentials."""

    def __init__(self, *, home=None, platform=None):
        self.home, self.platform = home, platform

    @contextmanager
    def _store(self, *, include_root=False):
        if not persistence_supported(self.platform):
            raise ReaderError('STORE_UNAVAILABLE')
        try:
            path = (resolve_diagnostics_home(platform=self.platform) if self.home is None else
                    resolve_diagnostics_home(environ={'LLMTOOL_HOME': str(self.home)}, platform=self.platform))
        except LLMToolError:
            raise ReaderError('STORE_UNSAFE') from None
        with _home(path) as root:
            try:
                runs = _directory(root, 'runs')
            except FileNotFoundError:
                raise _AbsentStore() from None
            try:
                # Root before run: same order as writer cleanup; root wait is bounded.
                with _lock(root, '.store.lock') as acquired:
                    if not acquired:
                        raise ReaderError('STORE_UNAVAILABLE')
                    yield (root, runs) if include_root else runs
                    _check_link(root, 'runs', runs, True)
            finally:
                os.close(runs)

    @contextmanager
    def _run(self, runs, run_id):
        directory = _directory(runs, run_id)
        try:
            with _lock(directory, '.run.lock') as acquired:
                # Publication changes names/link counts under the writer's lock.
                # A busy run is incomplete; do not inspect its mutable artifacts.
                names, snapshots = set(), {}
                if acquired:
                    names = set(_names(directory, RUN_FILE_SCAN_LIMIT))
                    if not names <= RUN_FILES or '.run.lock' not in names:
                        raise ReaderError('FORMAT_INVALID')
                    for name in names:
                        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                        _private(info)
                        snapshots[name] = _snapshot(info)
                yield directory, names, acquired
                _check_link(runs, run_id, directory, True)
                if acquired:
                    if set(_names(directory, RUN_FILE_SCAN_LIMIT)) != names:
                        raise ReaderError('STORE_UNAVAILABLE')
                    for name, original in snapshots.items():
                        if _snapshot(os.stat(name, dir_fd=directory, follow_symlinks=False)) != original:
                            raise ReaderError('STORE_UNAVAILABLE')
        finally:
            os.close(directory)

    def runs(self):
        result = {'schema': 'llmtool.diagnostics.runs', 'schema_version': 1,
                  'runs': [], 'damaged_count': 0, 'unknown_count': 0, 'truncated': False}
        used = 0
        try:
            with self._store() as runs:
                names = _names(runs, RUN_SCAN_LIMIT)
                for name in names:
                    try:
                        d.validate_run_id(name)
                    except ValueError:
                        result['unknown_count'] += 1
                        continue
                    try:
                        with self._run(runs, name) as (directory, files, acquired):
                            if not acquired or 'summary.json' not in files:
                                row = _incomplete(name)
                            else:
                                if 'trace.jsonl' not in files or 'trace.partial.jsonl' in files:
                                    raise ReaderError('FORMAT_INVALID')
                                raw = _read(directory, 'summary.json', SUMMARY_MAX_BYTES)
                                used += len(raw)
                                if used > RUNS_READ_MAX_BYTES:
                                    raise ReaderError('LIMIT_EXCEEDED')
                                row = _summary_projection(_summary(raw, name))
                        result['runs'].append(row)
                    except ReaderError:
                        if used > RUNS_READ_MAX_BYTES:
                            raise
                        result['damaged_count'] += 1
                    except (OSError, ValueError, TypeError, KeyError):
                        result['damaged_count'] += 1
                if set(_names(runs, RUN_SCAN_LIMIT)) != set(names):
                    raise ReaderError('STORE_UNAVAILABLE')
        except _AbsentStore:
            pass  # Only absent home/runs is empty; never hide a vanished snapshot.
        except ReaderError:
            raise
        except (OSError, ValueError, TypeError, KeyError):
            raise ReaderError('STORE_UNAVAILABLE') from None
        result['runs'].sort(key=lambda row: (row['started_at'] or '', row['run_id']), reverse=True)
        result['truncated'] = len(result['runs']) > RUN_OUTPUT_LIMIT
        result['runs'] = result['runs'][:RUN_OUTPUT_LIMIT]
        return result

    def inspect_store(self):
        """Read-only structure, permissions and bounded summary-metadata check.

        A busy run's artifacts are deliberately not inspected. Unknown names
        are counted without displaying, following or repairing them. Completed
        runs are validated from their summary metadata only; trace evidence is
        never read, so this stays neither a completion check nor proof that
        persistence can be written. Malformed metadata is reported as degraded
        instead of healthy.
        """
        result = {'state': 'absent', 'run_count': 0, 'active_count': 0,
                  'unknown_count': 0, 'damaged_count': 0, 'metadata': 'ok',
                  'staging_count': 0, 'staging_bytes': 0, 'root_unknown_count': 0,
                  'root_unknown_bytes': 0,
                  'staging_scan_limited': False}
        if not persistence_supported(self.platform):
            result.update(state='unsupported', metadata='unsupported')
            return result
        try:
            with self._store(include_root=True) as (root, runs):
                result['state'] = 'present'
                root_names = []
                with os.scandir(root) as entries:
                    for item in entries:
                        if len(root_names) >= ROOT_SCAN_LIMIT:
                            result['staging_scan_limited'] = True
                            break
                        root_names.append(item.name)
                budget = [SCAN_LIMIT - len(root_names)]
                if budget[0] < 0:
                    result['staging_scan_limited'] = True
                    budget[0] = 0
                for name in root_names:
                    if name in ('.store.lock', 'runs'):
                        continue
                    if name.startswith('.eviction-') and name.endswith('.json'):
                        try:
                            d.validate_run_id(name[len('.eviction-'):-len('.json')])
                            continue
                        except ValueError:
                            pass
                    if re.fullmatch(r'\.run-init-[0-9a-f]{32}', name):
                        try:
                            info = os.stat(name, dir_fd=root, follow_symlinks=False)
                            _private(info, True)
                            stage = _directory(root, name)
                            try:
                                if _identity(info) != _identity(os.fstat(stage)):
                                    raise ReaderError('STORE_UNAVAILABLE')
                                try:
                                    files = _names(stage, budget[0] + 1)
                                    budget[0] -= len(files)
                                    if budget[0] < 0:
                                        raise ReaderError('LIMIT_EXCEEDED')
                                    for filename in files:
                                        try:
                                            result['staging_bytes'] += _account_entry(
                                                stage, filename, budget)
                                        except ReaderError as exc:
                                            if exc.code == 'LIMIT_EXCEEDED':
                                                result['staging_scan_limited'] = True
                                                break
                                            raise
                                        except OSError:
                                            result['staging_scan_limited'] = True
                                            break
                                except ReaderError as exc:
                                    if exc.code == 'LIMIT_EXCEEDED':
                                        result['staging_scan_limited'] = True
                                    else:
                                        raise
                                except OSError:
                                    result['staging_scan_limited'] = True
                                result['staging_count'] += 1
                                _check_link(root, name, stage, True)
                            finally:
                                os.close(stage)
                        except ReaderError:
                            raise ReaderError('STORE_UNAVAILABLE') from None
                        except OSError:
                            raise ReaderError('STORE_UNAVAILABLE') from None
                        continue
                    result['root_unknown_count'] += 1
                    try:
                        result['root_unknown_bytes'] += _account_entry(root, name, budget)
                    except ReaderError as exc:
                        if exc.code == 'LIMIT_EXCEEDED':
                            result['staging_scan_limited'] = True
                        else:
                            raise
                    except OSError:
                        result['staging_scan_limited'] = True
                names = _names(runs, RUN_SCAN_LIMIT)
                budget[0] -= len(names)
                if budget[0] < 0:
                    result['staging_scan_limited'] = True
                    budget[0] = 0
                for name in names:
                    try:
                        d.validate_run_id(name)
                    except ValueError:
                        result['unknown_count'] += 1
                        continue
                    with self._run(runs, name) as (directory, files, acquired):
                        result['run_count'] += 1
                        budget[0] -= len(files)
                        if budget[0] < 0:
                            result['staging_scan_limited'] = True
                            budget[0] = 0
                        if not acquired:
                            result['active_count'] += 1
                            continue
                        for filename in files:
                            info = os.stat(filename, dir_fd=directory, follow_symlinks=False)
                            limit = (SUMMARY_MAX_BYTES if filename == 'summary.json' else
                                     RESERVATION_MAX_BYTES if filename == '.reservation' else
                                     TRACE_MAX_BYTES if filename in ('trace.jsonl', 'trace.partial.jsonl')
                                     else 0)
                            if info.st_size > limit:
                                raise ReaderError('LIMIT_EXCEEDED')
                        if 'summary.json' in files and not _metadata_sound(directory, files, name):
                            result['damaged_count'] += 1
                if set(_names(runs, RUN_SCAN_LIMIT)) != set(names):
                    raise ReaderError('STORE_UNAVAILABLE')
        except _AbsentStore:
            pass
        except ReaderError:
            raise
        except (OSError, ValueError, TypeError, KeyError):
            raise ReaderError('STORE_UNAVAILABLE') from None
        result['metadata'] = 'degraded' if result['damaged_count'] else 'ok'
        return result

    def trace(self, run_id, *, errors=False):
        try:
            d.validate_run_id(run_id)
        except ValueError:
            raise ReaderError('ID_INVALID') from None
        result = {'schema': 'llmtool.diagnostics.trace', 'schema_version': 1,
                  'run_id': run_id, 'state': 'incomplete', 'summary': None, 'events': [],
                  'filtered': bool(errors), 'omitted_partial_line': False}
        try:
            with self._store() as runs:
                with self._run(runs, run_id) as (directory, names, acquired):
                    if acquired:
                        complete = 'summary.json' in names
                        if complete and ('trace.jsonl' not in names or 'trace.partial.jsonl' in names):
                            raise ReaderError('FORMAT_INVALID')
                        if 'trace.jsonl' in names and 'trace.partial.jsonl' in names:
                            raise ReaderError('FORMAT_INVALID')
                        filename = ('trace.jsonl' if 'trace.jsonl' in names else
                                    'trace.partial.jsonl' if 'trace.partial.jsonl' in names else None)
                        raw = b'' if filename is None else _read(directory, filename, TRACE_MAX_BYTES)
                        events, omitted = _events(raw, run_id, complete)
                        if complete:
                            summary = _summary(_read(directory, 'summary.json', SUMMARY_MAX_BYTES), run_id)
                            _consistent(summary, raw, events)
                            result.update(state='complete', summary=_summary_projection(summary))
                        result.update(events=events, omitted_partial_line=omitted)
        except ReaderError:
            raise
        except (_AbsentStore, FileNotFoundError):
            raise ReaderError('NOT_FOUND') from None
        except OSError:
            raise ReaderError('STORE_UNAVAILABLE') from None
        except (ValueError, TypeError, KeyError):
            raise ReaderError('FORMAT_INVALID') from None
        if errors:
            result['events'] = [event for event in result['events'] if event['type'] in ('error', 'warning')]
        return result
