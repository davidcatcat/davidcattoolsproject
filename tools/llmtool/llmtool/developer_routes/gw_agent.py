"""Adapter for the separately installed, bounded API Agent Gateway."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile

from .base import DeveloperRoute, RouteError
from .. import config as llm_config

_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z')
_TASK_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_STATUS_EXIT = {'DONE': 0, 'BLOCKED': 3, 'MODEL_ERROR': 4,
                'POLICY_REFUSED': 5, 'BUDGET_EXCEEDED': 6}
_AUDIT_RETENTION_SECONDS = 30 * 24 * 60 * 60


def cleanup_audit_copies(*, now=None, temp_root=None):
    """Remove llmtool-owned audit copies older than the 30-day retention window."""
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    root = Path(temp_root or tempfile.gettempdir())
    removed = []
    try:
        candidates = list(root.glob('llmtool-gw-agent-audit-*'))
    except OSError:
        return removed
    for directory in candidates:
        try:
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
                    now - info.st_mtime < _AUDIT_RETENTION_SECONDS):
                continue
            children = list(directory.iterdir())
            if len(children) != 1 or children[0].name != 'audit.jsonl':
                continue
            child_info = children[0].lstat()
            if (not stat.S_ISREG(child_info.st_mode) or child_info.st_uid != os.getuid() or
                    child_info.st_nlink != 1):
                continue
            children[0].unlink()
            directory.rmdir()
            removed.append(directory.name)
        except (OSError, ValueError):
            continue
    return removed


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _scope_digest(task):
    # Mirrors Task.scope_fields() and the README's canonical JSON golden vector.
    scope = {
        'read_paths': sorted(set(task['read_paths'])),
        'write_paths': sorted(set(task['write_paths'])),
        'egress_basis': task['egress_basis'],
        'data_class': task['data_class'],
        'budgets': dict(sorted(task['budgets'].items())),
    }
    return hashlib.sha256(_canonical(scope)).hexdigest()


class GwAgentRoute(DeveloperRoute):
    name = 'gw_agent'
    worker_key = 'gw'
    family = 'api-agent-gateway'

    def __init__(self, config):
        super().__init__(config)
        package = config.get('package_path')
        profile = config.get('profile')
        profile_name = config.get('profile_name')
        authorization_ref = config.get('authorization_ref')
        expected = config.get('expected_served_model', config.get('default_model'))
        if (type(package) is not str or not os.path.isabs(package) or
                type(profile) is not dict or type(profile_name) is not str or not profile_name or
                type(authorization_ref) is not str or not authorization_ref.strip() or
                type(expected) is not str or _MODEL.fullmatch(expected) is None):
            raise RouteError('GW_AGENT_CONFIG_INVALID')
        if profile.get('auth_env') != 'GW_AGENT_API_KEY' or profile.get('model') != expected:
            raise RouteError('GW_AGENT_CONFIG_INVALID')
        self.package_path = str(Path(package).resolve(strict=True))
        if not (Path(self.package_path) / 'api_agent_gateway').is_dir():
            raise RouteError('GW_AGENT_CONFIG_INVALID')
        self.profile = dict(profile)
        self.profile_name = profile_name
        self.authorization_ref = authorization_ref
        self.expected_served_model = expected
        classes = config.get('allowed_data_classes')
        if (type(classes) is not list or not classes or
                any(type(item) is not str or not item for item in classes) or
                len(set(classes)) != len(classes)):
            raise RouteError('GW_AGENT_CONFIG_INVALID')
        self.allowed_data_classes = frozenset(classes)
        self._result = None
        self.audit_copy_path = None

    @property
    def default_model(self):
        return self.expected_served_model

    @property
    def default_effort(self):
        return self.config.get('default_effort', 'low')

    def validate_profile(self, model, effort):
        if (type(model) is not str or _MODEL.fullmatch(model) is None or
                model != self.expected_served_model or type(effort) is not str or
                effort not in ('minimal', 'low', 'medium', 'high')):
            raise RouteError('GW_AGENT_PROFILE_INVALID')
        return model, effort

    def validate_request(self, request):
        value = super().validate_request(request)
        task = value.get('task')
        if (type(task) is not dict or
                type(task.get('task_id')) is not str or _TASK_ID.fullmatch(task['task_id']) is None or
                task.get('source_sha') != value['expected_head'] or
                type(task.get('read_paths')) is not list or type(task.get('write_paths')) is not list or
                tuple(task['write_paths']) != value['write_paths'] or
                type(task.get('budgets')) is not dict or
                any(type(budget) is not int for budget in task['budgets'].values()) or
                type(task.get('egress_basis')) is not str or not task['egress_basis'] or
                type(task.get('data_class')) is not str or
                task.get('data_class') not in self.allowed_data_classes):
            raise RouteError('GW_AGENT_REQUEST_INVALID')
        if any(type(path) is not str for path in task['read_paths'] + task['write_paths']):
            raise RouteError('GW_AGENT_REQUEST_INVALID')
        return value

    def prepare_files(self, tmpdir, request):
        root = Path(request['root']).resolve(strict=True)
        private_tmp = Path(tmpdir).resolve(strict=True)
        package_path = Path(self.package_path)
        if (package_path == root or root in package_path.parents or package_path in root.parents or
                private_tmp == root or root in private_tmp.parents):
            raise RouteError('GW_AGENT_CONFIG_INVALID')
        task = dict(request['task'])
        try:
            serialized_task = _canonical(task)
            serialized_profile = _canonical({'schema_version': 1,
                                              'profiles': {self.profile_name: self.profile}})
        except (TypeError, ValueError):
            raise RouteError('GW_AGENT_REQUEST_INVALID') from None
        auth = {
            'schema_version': 1,
            'authorization_ref': self.authorization_ref,
            'checked_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'scope_digest': _scope_digest(task),
            'task_id': task['task_id'],
            'egress_basis': task['egress_basis'],
            'data_class': task['data_class'],
            'profile': self.profile_name,
            'session_id': None,
        }
        files = {
            'task.json': serialized_task,
            'authorization.json': _canonical(auth),
            'profiles.json': serialized_profile,
        }
        for name, payload in files.items():
            path = Path(tmpdir) / name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(payload)
                stream.write(b'\n')
            os.chmod(path, 0o600)

    def build_argv(self, executable, request):
        tmp = request['_route_tmpdir']
        return [sys.executable, '-B', '-P', '-m', 'api_agent_gateway', 'run',
                '--authorization-file', str(Path(tmp) / 'authorization.json'),
                '--workdir', request['root'], '--profile', self.profile_name,
                '--task-file', str(Path(tmp) / 'task.json'),
                '--result-file', str(Path(tmp) / 'result.json'),
                '--audit-file', str(Path(tmp) / 'audit.jsonl'),
                '--profiles-file', str(Path(tmp) / 'profiles.json'),
                '--state-dir', str(Path(tmp) / 'state')]

    def build_env(self, tmpdir):
        try:
            config_value = llm_config.resolve_profile_config('company-gw')
            credential = llm_config.api_key(config_value)
        except Exception:
            raise RouteError('GW_AGENT_CREDENTIAL_MISSING') from None
        if (type(credential) is not str or not credential or '\x00' in credential or
                len(credential) > 8192):
            raise RouteError('GW_AGENT_CREDENTIAL_MISSING')
        env = {key: os.environ[key] for key in
               ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE')
               if key in os.environ}
        env['TMPDIR'] = tmpdir
        env['PYTHONPATH'] = self.package_path
        env['GW_AGENT_API_KEY'] = credential
        return env

    def parse_output(self, stdout, stderr, log_text):
        result = self._result
        required = {'schema_version', 'status', 'code', 'run_id', 'session_id', 'task_id',
                    'changed_paths', 'summary', 'served_model', 'usage', 'turns',
                    'budget_used', 'audit_sha256'}
        if (type(result) is not dict or not required.issubset(result) or
                type(result.get('schema_version')) is not int or result['schema_version'] != 1 or
                result.get('status') not in _STATUS_EXIT or
                (result.get('code') is not None and type(result['code']) is not str) or
                (result.get('code') is not None and
                 re.fullmatch(r'[A-Z0-9_]{1,64}', result['code']) is None) or
                type(result.get('changed_paths')) is not list or
                any(type(path) is not str or not path or path.startswith('/') or
                    '\\' in path or any(part in ('', '.', '..') for part in path.split('/'))
                    for path in result['changed_paths']) or
                type(result.get('summary')) is not str or
                type(result.get('served_model')) is not str or
                type(result.get('task_id')) is not str or
                type(result.get('run_id')) is not str or
                re.fullmatch(r'[0-9a-f]{24}', result['run_id']) is None or
                (result.get('session_id') is not None and type(result['session_id']) is not str) or
                type(result.get('usage')) is not dict or type(result.get('turns')) is not int or
                (result.get('budget_used') is not None and type(result['budget_used']) is not dict) or
                type(result.get('audit_sha256')) is not str or
                re.fullmatch(r'[0-9a-f]{64}', result['audit_sha256']) is None):
            raise RouteError('GW_AGENT_OUTPUT_INVALID')
        if ((result['status'] == 'DONE' and result['code'] is not None) or
                (result['status'] != 'DONE' and
                 (type(result['code']) is not str or not result['code']))):
            raise RouteError('GW_AGENT_OUTPUT_INVALID')
        self._status = result['status']
        self._gateway_code = result['code']
        return {'status': result['status'], 'summary': result['summary'][:4000],
                'session_id': result.get('session_id'), 'usage': result.get('usage'),
                'changed_paths': result['changed_paths']}

    def validate_exit(self, exit_code, parsed):
        if type(exit_code) is not int or exit_code != _STATUS_EXIT.get(self._status):
            raise RouteError('GW_AGENT_OUTPUT_INVALID',
                             facts={'gateway_code': self._gateway_code})

    def validate_status(self, parsed):
        if self._status != 'DONE':
            raise RouteError('GW_AGENT_' + self._status,
                             facts={'gateway_code': self._gateway_code})

    def _verified_audit_digest(self, path):
        """Load the verifier from the configured gateway package, isolated by name."""
        package_dir = Path(self.package_path) / 'api_agent_gateway'
        module_name = '_llmtool_gateway_audit_' + hashlib.sha256(
            os.fsencode(str(package_dir))).hexdigest()[:16]
        package_spec = importlib.util.spec_from_file_location(
            module_name, package_dir / '__init__.py',
            submodule_search_locations=[str(package_dir)])
        if package_spec is None or package_spec.loader is None:
            raise ValueError
        package = importlib.util.module_from_spec(package_spec)
        loaded = []
        try:
            sys.modules[module_name] = package
            loaded.append(module_name)
            package_spec.loader.exec_module(package)
            audit = importlib.import_module(module_name + '.audit')
            loaded.append(module_name + '.audit')
            digest = audit.verify(path)
            if type(digest) is not str or re.fullmatch(r'[0-9a-f]{64}', digest) is None:
                raise ValueError
            return digest
        finally:
            for name in reversed(loaded):
                sys.modules.pop(name, None)

    def verified_model(self, log_text, requested):
        if not isinstance(self._result, dict):
            return None
        model = self._result.get('served_model')
        if type(model) is not str or model == 'UNKNOWN':
            return None
        return model

    def quota_exhausted(self, parsed, stderr):
        return self._gateway_code == 'HTTP_429'

    def collect(self, tmpdir, request):
        root = Path(request['root']).resolve(strict=True)
        result_path = Path(tmpdir) / 'result.json'
        try:
            result_info = result_path.lstat()
            if (not stat.S_ISREG(result_info.st_mode) or result_info.st_nlink != 1 or
                    result_info.st_uid != os.getuid() or stat.S_IMODE(result_info.st_mode) != 0o600):
                raise ValueError
            raw = result_path.read_bytes()
            if len(raw) > 1024 * 1024:
                raise ValueError
            self._result = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if (type(self._result) is not dict or
                    self._result.get('task_id') != request['task']['task_id']):
                raise RouteError('GW_AGENT_OUTPUT_INVALID')
            source = Path(tmpdir) / 'audit.jsonl'
            info = source.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError
            audit = source.read_bytes()
            if len(audit) > 4 * 1024 * 1024:
                raise ValueError
            lines = audit.splitlines()
            if not lines or len(lines) != len([line for line in lines if line.strip()]):
                raise ValueError
            expected = self._result.get('audit_sha256') if type(self._result) is dict else None
            try:
                actual = self._verified_audit_digest(source)
            except Exception:
                raise RouteError('GW_AGENT_AUDIT_INVALID') from None
            if (type(expected) is not str or not re.fullmatch(r'[0-9a-f]{64}', expected) or
                    expected != actual):
                raise RouteError('GW_AGENT_AUDIT_INVALID')
            # Audit copies are retained outside the D6 and pruned after 30 days.
            audit_dir = tempfile.mkdtemp(prefix='llmtool-gw-agent-audit-')
            os.chmod(audit_dir, 0o700)
            if Path(audit_dir).resolve().is_relative_to(root):
                shutil.rmtree(audit_dir, ignore_errors=True)
                raise ValueError
            target = Path(audit_dir) / 'audit.jsonl'
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(audit)
            self.audit_copy_path = str(target)
        except RouteError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
            raise RouteError('GW_AGENT_OUTPUT_INVALID') from None

