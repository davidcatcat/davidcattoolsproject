"""Owner-private durable storage for llmtool PM-intake tasks.

This is a local task record store, not an execution queue or authorization
ledger. A task's logical PM session is an llmtool identifier; it is not a claim
that the selected provider exposes a resumable native session.
"""

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .action_contract import TaskBinding
from .diagnostics import validate_provider_usage
from .errors import LLMToolError
from .task_verification import parse_task_contract
from .test_output_excerpts import _emitted_sensitive

SCHEMA_VERSION = 2
MAX_RECORD_BYTES = 1024 * 1024
TASK_ID = re.compile(r'[0-9a-f]{32}')
SESSION_ID = re.compile(r'pm-[a-z0-9-]{1,60}')
DEV_SESSION_ID = re.compile(r'dev-[a-z0-9-]{1,60}')
STATES = frozenset(('PM_PENDING', 'PM_RESPONDED', 'PM_UNAVAILABLE',
                     'INPUT_REJECTED', 'PM_RESPONSE_REJECTED'))
_FIELDS_V1 = frozenset((
    'schema_version', 'task_id', 'run_id', 'pm_session_id',
    'pm_provider_profile', 'pm_provider_type', 'pm_model',
    'backend_session_semantics', 'native_session_id', 'state',
    'created_at', 'updated_at', 'task_contract', 'task_binding',
    'request', 'request_sha256', 'response', 'usage', 'error_code',
    'record_sha256',
))
_FIELDS_V2 = _FIELDS_V1 | frozenset(('developer', 'pm_assignment'))
FIELDS = _FIELDS_V2
_USAGE_FIELDS = frozenset(('prompt_tokens', 'completion_tokens', 'total_tokens'))
_DEVELOPER_FIELDS = frozenset((
    'assignment_id', 'selection_source', 'state', 'provider_profile',
    'provider_type', 'model', 'backend_session_semantics', 'session_id',
    'source_sha', 'source_sha_binding', 'read_paths', 'write_paths', 'acceptance', 'context_sha256',
    'egress_basis', 'input_bytes', 'max_tokens',
    'artifact_format', 'artifact', 'artifact_sha256', 'usage', 'error_code',
    'created_at',
))
_PM_ASSIGNMENT_FIELDS = frozenset(('schema_version', 'route', 'risk', 'goal', 'acceptance'))


def _fail(code='TASK_STORE_INVALID'):
    raise LLMToolError('task store unavailable; details omitted') from None


def new_id():
    return uuid.uuid4().hex


def default_root():
    return Path.home() / '.local' / 'state' / 'llmtool' / 'tasks'


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'


def _digest(record):
    value = {key: item for key, item in record.items() if key != 'record_sha256'}
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _safe_identifier(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _validate(record):
    if (type(record) is not dict or
            (record.get('schema_version') == 1 and set(record) != _FIELDS_V1) or
            (record.get('schema_version') == 2 and set(record) != _FIELDS_V2) or
            record.get('schema_version') not in (1, 2)):
        _fail()
    if (type(record['schema_version']) is not int or record['schema_version'] not in (1, 2) or
            not _safe_identifier(record['task_id'], TASK_ID) or
            not _safe_identifier(record['run_id'], TASK_ID) or
            not _safe_identifier(record['pm_session_id'], SESSION_ID) or
            type(record['state']) is not str or record['state'] not in STATES or
            type(record['created_at']) is not str or type(record['updated_at']) is not str):
        _fail()
    for key in ('created_at', 'updated_at'):
        try:
            stamp = datetime.strptime(record[key], '%Y-%m-%dT%H:%M:%SZ')
        except (TypeError, ValueError):
            _fail()
        if stamp.strftime('%Y-%m-%dT%H:%M:%SZ') != record[key]:
            _fail()
    for key in ('pm_provider_profile', 'pm_provider_type', 'pm_model',
                'backend_session_semantics', 'error_code'):
        value = record[key]
        if value is not None and (type(value) is not str or len(value) > 256):
            _fail()
    if (type(record['pm_provider_profile']) is not str or
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', record['pm_provider_profile']) is None):
        _fail()
    if record['pm_provider_type'] is not None and re.fullmatch(
            r'[a-z][a-z0-9-]{0,63}', record['pm_provider_type']) is None:
        _fail()
    if record['pm_model'] is not None and re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}', record['pm_model']) is None:
        _fail()
    if record['native_session_id'] is not None:
        _fail()
    if record['backend_session_semantics'] not in (
            'EPHEMERAL_SINGLE_CALL', 'NO_NATIVE_SESSION_ID_EXPOSED', 'NOT_STARTED'):
        _fail()
    contract_raw = record['task_contract']
    if type(contract_raw) is not dict:
        _fail()
    try:
        contract = parse_task_contract(json.dumps(contract_raw, ensure_ascii=True,
                                                   separators=(',', ':')))
        binding = TaskBinding(**record['task_binding'])
    except Exception:
        _fail()
    if (contract.task_id != record['task_id'] or
            binding != contract.binding(record['run_id'])):
        _fail()
    state = record['state']
    request = record['request']
    response = record['response']
    if request is not None:
        try:
            request_size = len(request.encode('utf-8'))
        except (AttributeError, UnicodeError):
            _fail()
        if type(request) is not str or request_size > 64 * 1024:
            _fail()
    if response is not None:
        try:
            response_size = len(response.encode('utf-8'))
        except (AttributeError, UnicodeError):
            _fail()
        if type(response) is not str or response_size > 64 * 1024:
            _fail()
    if any(_emitted_sensitive(text) for text in (request, response) if text is not None):
        _fail()
    if state == 'INPUT_REJECTED':
        if record['request_sha256'] is not None:
            _fail()
    elif (type(record['request_sha256']) is not str or
          re.fullmatch(r'[0-9a-f]{64}', record['request_sha256']) is None):
        _fail()
    if request is not None and hashlib.sha256(request.encode('utf-8')).hexdigest() != record['request_sha256']:
        _fail()
    if type(record['usage']) is dict:
        try:
            if set(record['usage']) != _USAGE_FIELDS:
                _fail()
            validate_provider_usage(record['usage'])
        except Exception:
            _fail()
    elif record['usage'] is not None:
        _fail()
    if record['error_code'] is not None and re.fullmatch(r'[A-Z0-9_]{1,64}', record['error_code']) is None:
        _fail()
    if record['schema_version'] == 2:
        assignment = record['pm_assignment']
        if assignment is not None:
            if (type(assignment) is not dict or set(assignment) != _PM_ASSIGNMENT_FIELDS or
                    type(assignment['schema_version']) is not int or assignment['schema_version'] != 1 or
                    assignment['route'] not in ('company-gw', 'codex', 'opencode', 'none') or
                    assignment['risk'] not in ('LOW', 'OTHER') or
                    type(assignment['goal']) is not str or not assignment['goal'] or
                    len(assignment['goal'].encode('utf-8')) > 512 or
                    _emitted_sensitive(assignment['goal']) or
                    type(assignment['acceptance']) is not list or
                    not 1 <= len(assignment['acceptance']) <= 16 or
                    any(type(item) is not str or not item or len(item.encode('utf-8')) > 512 or
                        _emitted_sensitive(item) for item in assignment['acceptance'])):
                _fail()
        developer = record['developer']
        if developer is not None:
            if type(developer) is not dict or set(developer) != _DEVELOPER_FIELDS:
                _fail()
            if (not _safe_identifier(developer['assignment_id'], TASK_ID) or
                    developer['selection_source'] not in ('PM', 'USER') or
                    developer['state'] not in ('DEVELOPER_PENDING', 'DEVELOPER_DISPATCHING',
                                               'DEVELOPER_RESPONDED',
                                               'DEVELOPER_UNAVAILABLE', 'DEVELOPER_OUTCOME_UNKNOWN',
                                               'DEVELOPER_OUTPUT_REJECTED') or
                    developer['provider_profile'] != 'company-gw' or
                    developer['provider_type'] not in (None, 'openai-compatible') or
                    developer['backend_session_semantics'] != 'NO_NATIVE_SESSION_ID_EXPOSED' or
                    not _safe_identifier(developer['session_id'], DEV_SESSION_ID) or
                    type(developer['source_sha']) is not str or
                    re.fullmatch(r'[0-9a-f]{40}', developer['source_sha']) is None or
                    developer['source_sha_binding'] != 'UNVERIFIED_CALLER_INPUT' or
                    type(developer['context_sha256']) is not str or
                    re.fullmatch(r'[0-9a-f]{64}', developer['context_sha256']) is None or
                    type(developer['egress_basis']) is not str or
                    not developer['egress_basis'] or len(developer['egress_basis'].encode('utf-8')) > 512 or
                    _emitted_sensitive(developer['egress_basis']) or
                    type(developer['input_bytes']) is not int or
                    not 1 <= developer['input_bytes'] <= 64 * 1024 or
                    (developer['max_tokens'] is not None and
                     (type(developer['max_tokens']) is not int or
                      not 1 <= developer['max_tokens'] <= 1_000_000)) or
                    developer['artifact_format'] != 'unified_diff' or
                    type(developer['created_at']) is not str):
                _fail()
            try:
                stamp = datetime.strptime(developer['created_at'], '%Y-%m-%dT%H:%M:%SZ')
            except (TypeError, ValueError):
                _fail()
            if stamp.strftime('%Y-%m-%dT%H:%M:%SZ') != developer['created_at']:
                _fail()
            model = developer['model']
            if model is not None and (type(model) is not str or len(model) > 256):
                _fail()
            for field in ('read_paths', 'write_paths'):
                values = developer[field]
                if (type(values) is not list or not values or len(values) > 32 or
                        any(type(item) is not str or not item or len(item) > 512 or
                            item.startswith('/') or '\\' in item or
                            any(part in ('', '.', '..') for part in item.split('/')) or
                            any(ord(char) < 0x20 or ord(char) >= 0x7f for char in item)
                            for item in values) or values != sorted(set(values))):
                    _fail()
            if not set(developer['write_paths']).issubset(developer['read_paths']):
                _fail()
            acceptance = developer['acceptance']
            if (type(acceptance) is not list or not 1 <= len(acceptance) <= 16 or
                    any(type(item) is not str or not item or len(item.encode('utf-8')) > 512
                        for item in acceptance)):
                _fail()
            artifact = developer['artifact']
            artifact_sha = developer['artifact_sha256']
            if artifact is not None:
                if (type(artifact) is not str or len(artifact.encode('utf-8')) > 64 * 1024 or
                        type(artifact_sha) is not str or
                        hashlib.sha256(artifact.encode('utf-8')).hexdigest() != artifact_sha or
                        _emitted_sensitive(artifact)):
                    _fail()
            elif artifact_sha is not None:
                _fail()
            usage = developer['usage']
            if usage is not None:
                try:
                    if type(usage) is not dict or set(usage) != _USAGE_FIELDS:
                        _fail()
                    validate_provider_usage(usage)
                except Exception:
                    _fail()
            if developer['error_code'] is not None and (
                    type(developer['error_code']) is not str or
                    re.fullmatch(r'[A-Z0-9_]{1,64}', developer['error_code']) is None):
                _fail()
            dev_state = developer['state']
            if dev_state == 'DEVELOPER_PENDING' and (
                    developer['provider_type'] is not None or developer['model'] is not None or
                    artifact is not None or usage is not None or developer['error_code'] is not None):
                _fail()
            if dev_state == 'DEVELOPER_DISPATCHING' and (
                    developer['provider_type'] != 'openai-compatible' or artifact is not None or
                    usage is not None or developer['error_code'] is not None or
                    developer['max_tokens'] is None):
                _fail()
            if dev_state == 'DEVELOPER_UNAVAILABLE' and (
                    artifact is not None or usage is not None or
                    developer['error_code'] is None):
                _fail()
            if dev_state == 'DEVELOPER_OUTCOME_UNKNOWN' and (
                    developer['provider_type'] != 'openai-compatible' or artifact is not None or
                    usage is not None or developer['error_code'] is None):
                _fail()
            if dev_state == 'DEVELOPER_RESPONDED' and (
                    developer['provider_type'] is None or artifact is None or
                    developer['error_code'] is not None or developer['max_tokens'] is None):
                _fail()
            if dev_state == 'DEVELOPER_OUTPUT_REJECTED' and (
                    developer['provider_type'] is None or artifact is not None or
                    developer['error_code'] != 'DEVELOPER_OUTPUT_REJECTED'):
                _fail()
    if state == 'PM_PENDING' and (request is None or response is not None or
                                  record['error_code'] is not None or record['usage'] is not None):
        _fail()
    if state == 'PM_UNAVAILABLE' and (request is None or response is not None or
                                      record['error_code'] is None or record['usage'] is not None):
        _fail()
    if state == 'INPUT_REJECTED' and (request is not None or response is not None or
                                      record['error_code'] != 'PM_INPUT_REJECTED' or
                                      record['backend_session_semantics'] != 'NOT_STARTED' or
                                      record['pm_provider_type'] is not None or record['usage'] is not None):
        _fail()
    if state == 'PM_RESPONSE_REJECTED' and (request is None or response is not None or
                                            record['error_code'] != 'PM_RESPONSE_REJECTED' or
                                            record['pm_provider_type'] is None or
                                            record['backend_session_semantics'] == 'NOT_STARTED' or
                                            record['usage'] is not None):
        _fail()
    if state == 'PM_RESPONDED' and (request is None or not response or
                                    record['error_code'] is not None or
                                    record['pm_provider_type'] is None or
                                    record['backend_session_semantics'] == 'NOT_STARTED'):
        _fail()
    if type(record['record_sha256']) is not str or record['record_sha256'] != _digest(record):
        _fail()
    return record


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
            info.st_gid, info.st_size, info.st_mtime_ns, info.st_nlink)


class TaskStore:
    """Owner-only, per-task JSON files with stable reads and atomic updates."""

    def __init__(self, root=None):
        self.root = Path(default_root() if root is None else root).absolute()

    def _ensure_root(self):
        if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'):
            _fail('TASK_STORE_UNAVAILABLE')
        current = Path(self.root.anchor)
        uid = os.getuid()
        for part in self.root.parts[1:]:
            current = current / part
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError:
                _fail('TASK_STORE_UNAVAILABLE')
            try:
                info = current.lstat()
            except OSError:
                _fail('TASK_STORE_UNAVAILABLE')
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                _fail('TASK_STORE_UNAVAILABLE')
            if info.st_uid not in (uid, 0):
                _fail('TASK_STORE_UNAVAILABLE')
            if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
                _fail('TASK_STORE_UNAVAILABLE')
        info = self.root.lstat()
        if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
            _fail('TASK_STORE_UNAVAILABLE')

    def _dirfd(self):
        self._ensure_root()
        try:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(fd)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) != 0o700):
                os.close(fd)
                _fail('TASK_STORE_UNAVAILABLE')
            return fd
        except LLMToolError:
            raise
        except OSError:
            _fail('TASK_STORE_UNAVAILABLE')

    @staticmethod
    def _name(task_id):
        if not _safe_identifier(task_id, TASK_ID):
            _fail()
        return task_id + '.json'

    def _read_at(self, directory, task_id):
        name = self._name(task_id)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                    stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
                    before.st_size > MAX_RECORD_BYTES):
                os.close(fd)
                _fail()
            with os.fdopen(fd, 'rb') as stream:
                raw = stream.read(MAX_RECORD_BYTES + 1)
                stream.seek(0)
                repeated = stream.read(MAX_RECORD_BYTES + 1)
                after = os.fstat(stream.fileno())
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if len(raw) > MAX_RECORD_BYTES or raw != repeated or _identity(before) != _identity(after) or _identity(before) != _identity(current):
                _fail()
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=self._unique_pairs,
                               parse_constant=lambda _: _fail())
            if value.get('task_id') != task_id:
                _fail()
            return _validate(value)
        except LLMToolError:
            raise
        except FileNotFoundError:
            raise LLMToolError('task not found') from None
        except Exception:
            _fail()

    @staticmethod
    def _unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail()
            result[key] = value
        return result

    @staticmethod
    def _encoded(record):
        value = dict(record)
        value['record_sha256'] = _digest(value)
        _validate(value)
        raw = _json_bytes(value)
        if len(raw) > MAX_RECORD_BYTES:
            _fail()
        return value, raw

    def _write(self, record, *, create):
        record = dict(record)
        record, raw = self._encoded(record)
        name = self._name(record['task_id'])
        directory = self._dirfd()
        temporary = '.' + record['task_id'] + '.' + uuid.uuid4().hex + '.tmp'
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            fd = os.open(temporary, flags, 0o600, dir_fd=directory)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if create:
                os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory,
                        follow_symlinks=False)
                os.unlink(temporary, dir_fd=directory)
            else:
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        except FileExistsError:
            _fail('TASK_STORE_CONFLICT')
        except LLMToolError:
            raise
        except OSError:
            _fail('TASK_STORE_UNAVAILABLE')
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
            os.close(directory)
        return record

    def create(self, record):
        return self._write(record, create=True)

    def get(self, task_id):
        directory = self._dirfd()
        try:
            return dict(self._read_at(directory, task_id))
        finally:
            os.close(directory)

    def update(self, task_id, **changes):
        directory = self._dirfd()
        lock = '.' + self._name(task_id) + '.lock'
        locked = False
        try:
            try:
                lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o600, dir_fd=directory)
            except FileExistsError:
                _fail('TASK_STORE_BUSY')
            os.close(lock_fd)
            locked = True
            current = self._read_at(directory, task_id)
            if set(changes) - (FIELDS - {'schema_version', 'task_id', 'run_id', 'pm_session_id',
                                         'created_at', 'task_contract', 'task_binding', 'record_sha256'}):
                _fail()
            current.update(changes)
            current['updated_at'] = datetime.now(timezone.utc).replace(
                microsecond=0).isoformat().replace('+00:00', 'Z')
            current['record_sha256'] = _digest(current)
            return self._write(current, create=False)
        finally:
            if locked:
                try:
                    os.unlink(lock, dir_fd=directory)
                except OSError:
                    pass
            os.close(directory)
