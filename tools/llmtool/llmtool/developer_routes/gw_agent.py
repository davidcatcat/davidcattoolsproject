"""Adapter for the separately installed, bounded API Agent Gateway."""

from __future__ import annotations

from datetime import datetime, timezone
import errno
import fcntl
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
from urllib.parse import urlsplit

from .base import DeveloperRoute, RouteError
from .write_boundary import SandboxExecBoundary
from .. import config as llm_config
from .. import github_identity

_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z')
_TASK_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_SOURCE_SHA = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_GATEWAY_DATA_CLASS = {'SYNTHETIC': 'synthetic', 'PUBLIC_REPO': 'public_repo'}
_GATEWAY_BUDGETS = {
    'max_turns': (1, 500), 'max_input_tokens': (1, 50_000_000),
    'max_output_tokens': (1, 5_000_000), 'max_wall_seconds': (1, 86_400),
    'max_tool_result_bytes': (256, 1_048_576),
    'max_sent_bytes': (1024, 512 * 1024 * 1024),
}
_STATUS_EXIT = {'DONE': 0, 'BLOCKED': 3, 'MODEL_ERROR': 4,
                'POLICY_REFUSED': 5, 'BUDGET_EXCEEDED': 6}
_AUDIT_RETENTION_SECONDS = 30 * 24 * 60 * 60
_MAX_USAGE_TOKENS = 50_000_000
_SESSION_FILE_MAX_BYTES = 16 * 1024 * 1024
# Names the company gateway routes on. They are not model identities: the
# provider answers with the model it really served, which is what gets recorded.
_ROUTING_ALIASES = frozenset({'gw'})
_AUDIT_TOOLS = frozenset({'list_dir', 'read_file', 'grep', 'apply_edit', 'finish'})
# The Task error code is at most 64 characters and starts with
# DEVELOPER_GW_AGENT_BLOCKED_, so a longer reason would be cut off.
_TOOL_FAILURE = re.compile(r'[A-Z0-9_]{1,37}\Z')
_SAFE_PATH_LAUNCHER = "import runpy, sys; del sys.path[0]; runpy.run_module('api_agent_gateway', run_name='__main__', alter_sys=True)"
_HAS_SAFE_PATH_FLAG = sys.version_info >= (3, 11)


def _normalize_gw_agent_usage(value):
    """Return the Task store's token triplet, or None unless fully reported."""
    if type(value) is not dict:
        return None
    if {'prompt_tokens', 'completion_tokens', 'total_tokens'} == set(value):
        prompt, completion = value['prompt_tokens'], value['completion_tokens']
    elif value.get('status') == 'REPORTED':
        prompt, completion = value.get('input_tokens'), value.get('output_tokens')
    else:
        return None
    if (type(prompt) is not int or type(completion) is not int or
            not 0 <= prompt <= _MAX_USAGE_TOKENS or
            not 0 <= completion <= _MAX_USAGE_TOKENS):
        return None
    return {'prompt_tokens': prompt, 'completion_tokens': completion,
            'total_tokens': prompt + completion}


def _last_tool_failure(audit_lines):
    """Return a bounded TOOL_CODE from verified audit metadata, or None.

    A failed write outranks later reads: a model that cannot write usually
    probes around afterwards, and those reads are symptoms. Audit records hold
    fixed tool names and codes, never model or provider text.
    """
    last = last_write = None
    for line in audit_lines:
        try:
            record = json.loads(line)
        except (UnicodeError, ValueError, RecursionError):
            continue
        if (type(record) is not dict or record.get('type') != 'tool' or
                record.get('ok') is not False or record.get('tool') not in _AUDIT_TOOLS or
                type(record.get('code')) is not str):
            continue
        reason = record['tool'].upper() + '_' + record['code']
        if _TOOL_FAILURE.fullmatch(reason) is None:
            continue
        last = reason
        if record['tool'] == 'apply_edit':
            last_write = reason
    return last_write or last


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
        expected = config.get('expected_served_model')
        default_model = config.get('default_model')
        if (type(package) is not str or
                (not os.path.isabs(package) and not package.startswith('~/')) or
                type(profile) is not dict or type(profile_name) is not str or not profile_name or
                type(authorization_ref) is not str or not authorization_ref.strip() or
                (expected is not None and
                 (type(expected) is not str or _MODEL.fullmatch(expected) is None)) or
                type(default_model) is not str or _MODEL.fullmatch(default_model) is None):
            raise RouteError('GW_AGENT_CONFIG_INVALID')
        if ('auth_env' not in profile or
                profile['auth_env'] not in (None, 'GW_AGENT_API_KEY') or
                profile.get('adapter') != 'openai-chat-tools' or
                type(profile.get('base_url')) is not str or
                type(profile.get('context_cap_tokens')) is not int or
                type(profile.get('max_output_tokens')) is not int or
                type(profile.get('allowed_data_classes')) is not list or
                type(profile.get('model')) is not str or
                _MODEL.fullmatch(profile['model']) is None or
                (expected is not None and profile['model'] != expected)):
            raise RouteError('GW_AGENT_CONFIG_INVALID')
        profile_classes = profile['allowed_data_classes']
        base_url = profile.get('base_url')
        parts = urlsplit(base_url) if type(base_url) is str else None
        if (not profile_classes or any(type(item) is not str or not item
                                       for item in profile_classes) or
                len(set(profile_classes)) != len(profile_classes) or
                parts is None or parts.scheme not in ('http', 'https') or
                not parts.hostname or parts.username is not None or parts.password is not None or
                '?' in base_url or '#' in base_url or
                profile['context_cap_tokens'] < 256 or
                not 1 <= profile['max_output_tokens'] < profile['context_cap_tokens']):
            raise RouteError('GW_AGENT_CONFIG_INVALID')
        try:
            expanded_package = os.path.expanduser(package) if package.startswith('~/') else package
            resolved_package = Path(expanded_package).resolve(strict=True)
            gateway_dir = resolved_package / 'api_agent_gateway'
            package_info = resolved_package.stat()
            gateway_info = gateway_dir.stat()
            package_is_valid = (
                stat.S_ISDIR(package_info.st_mode) and
                stat.S_ISDIR(gateway_info.st_mode) and
                package_info.st_uid == os.getuid() and
                gateway_info.st_uid == os.getuid() and
                not (stat.S_IMODE(package_info.st_mode) & 0o022) and
                not (stat.S_IMODE(gateway_info.st_mode) & 0o022) and
                resolved_package.is_dir() and gateway_dir.is_dir())
        except (OSError, RuntimeError, ValueError):
            raise RouteError('GW_AGENT_CONFIG_INVALID') from None
        if not package_is_valid:
            raise RouteError('GW_AGENT_CONFIG_INVALID')
        self.package_path = str(resolved_package)
        self.profile = {key: value for key, value in profile.items()
                        if key != 'data_class_map'}
        self.profile_name = profile_name
        self.authorization_ref = authorization_ref
        self.expected_served_model = expected
        self._default_model = default_model
        data_class_map = profile.get('data_class_map', _GATEWAY_DATA_CLASS)
        if (type(data_class_map) is not dict or data_class_map != _GATEWAY_DATA_CLASS or
                any(type(key) is not str or not key or type(value) is not str or not value
                    for key, value in data_class_map.items()) or
                len(set(data_class_map.values())) != len(data_class_map)):
            raise RouteError('GW_AGENT_CONFIG_INVALID')
        self.data_class_map = dict(data_class_map)
        self._validated_request = None
        self._credential_resolved = False
        self._credential = None
        classes = config.get('allowed_data_classes')
        if (type(classes) is not list or not classes or
                any(type(item) is not str or not item for item in classes) or
                len(set(classes)) != len(classes)):
            raise RouteError('GW_AGENT_CONFIG_INVALID')
        self.allowed_data_classes = frozenset(classes)
        self._result = None
        self._tool_failure = None
        self.audit_copy_path = None

    def configure_boundary(self, boundary):
        # The gateway writes through a temp file beside the target, so a literal
        # write path alone refuses every edit. Other boundaries stay fail-closed.
        if isinstance(boundary, SandboxExecBoundary):
            return boundary.with_gw_agent_tmp()
        return boundary

    @property
    def default_model(self):
        return self._default_model

    @property
    def default_effort(self):
        return self.config.get('default_effort', 'low')

    def validate_profile(self, model, effort):
        if (type(model) is not str or _MODEL.fullmatch(model) is None or
                (self.expected_served_model is not None and
                 model != self.expected_served_model) or type(effort) is not str or
                effort not in ('minimal', 'low', 'medium', 'high')):
            raise RouteError('GW_AGENT_PROFILE_INVALID')
        return model, effort

    def validate_request(self, request):
        value = super().validate_request(request)
        session_id = request.get('session_id')
        session_home = request.get('session_home')
        if session_id is not None:
            if type(session_id) is not str or _BINDING_SESSION_ID_READ.fullmatch(session_id) is None:
                raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')
            if session_home is None:
                raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')
        if session_home is not None:
            if type(session_home) is not str or not os.path.isabs(session_home):
                raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')
        if session_id is not None:
            value['session_id'] = session_id
        if session_home is not None:
            value['session_home'] = session_home
        self._validated_request = value
        task = value.get('task')
        if type(task) is not dict:
            raise RouteError('GW_AGENT_TASK_INVALID')
        required = {'schema_version', 'task_id', 'source_sha', 'goal', 'acceptance',
                    'read_paths', 'write_paths', 'egress_basis', 'data_class',
                    'budgets', 'findings'}
        if set(task) - required:
            raise RouteError('GW_AGENT_TASK_UNKNOWN_FIELDS')
        for field in sorted(required - {'findings'} - set(task)):
            raise RouteError('GW_AGENT_TASK_' + field.upper() + '_MISSING')
        task.setdefault('findings', [])
        if type(task.get('schema_version')) is not int or task['schema_version'] != 1:
            raise RouteError('GW_AGENT_TASK_SCHEMA_VERSION_INVALID')
        if (type(task.get('task_id')) is not str or
                _TASK_ID.fullmatch(task['task_id']) is None):
            raise RouteError('GW_AGENT_TASK_TASK_ID_INVALID')
        if (type(task.get('source_sha')) is not str or
                _SOURCE_SHA.fullmatch(task['source_sha']) is None or
                task['source_sha'] != value['expected_head']):
            raise RouteError('GW_AGENT_TASK_SOURCE_SHA_INVALID')
        if (type(task.get('goal')) is not str or not task['goal'].strip() or
                len(task['goal']) > 20000):
            raise RouteError('GW_AGENT_TASK_GOAL_INVALID')
        limits = {'acceptance': (50, 4000), 'read_paths': (500, 1024),
                  'write_paths': (500, 1024), 'findings': (50, 8000)}
        for field, (max_items, max_len) in limits.items():
            entries = task[field]
            if (type(entries) is not list or
                    len(entries) > max_items or
                    any(type(item) is not str or not item or len(item) > max_len
                        for item in entries)):
                raise RouteError('GW_AGENT_TASK_' + field.upper() + '_INVALID')
        if not task['read_paths'] or tuple(task['write_paths']) != value['write_paths']:
            raise RouteError('GW_AGENT_TASK_PATHS_INVALID')
        if (type(task.get('egress_basis')) is not str or
                not task['egress_basis'].strip() or len(task['egress_basis']) > 2000):
            raise RouteError('GW_AGENT_TASK_EGRESS_BASIS_INVALID')
        if type(task.get('data_class')) is not str:
            raise RouteError('GW_AGENT_TASK_DATA_CLASS_INVALID')
        mapped_data_class = self.data_class_map.get(task['data_class'])
        effective_data_class = mapped_data_class or task['data_class']
        if (effective_data_class not in self.allowed_data_classes or
                effective_data_class not in self.profile['allowed_data_classes']):
            raise RouteError('GW_AGENT_TASK_DATA_CLASS_INVALID')
        budgets = task.get('budgets')
        if type(budgets) is not dict:
            raise RouteError('GW_AGENT_TASK_BUDGETS_INVALID')
        for field, (minimum, maximum) in _GATEWAY_BUDGETS.items():
            amount = budgets.get(field)
            if type(amount) is not int or not minimum <= amount <= maximum:
                raise RouteError('GW_AGENT_TASK_BUDGET_' + field.upper() + '_INVALID')
        if set(budgets) != set(_GATEWAY_BUDGETS):
            raise RouteError('GW_AGENT_TASK_BUDGETS_INVALID')
        return value

    def prepare_files(self, tmpdir, request):
        try:
            self._prepare_files(tmpdir, request)
        except BaseException:
            self._credential = None
            self._credential_resolved = False
            raise

    def _prepare_files(self, tmpdir, request):
        root = Path(request['root']).resolve(strict=True)
        private_tmp = Path(tmpdir).resolve(strict=True)
        package_path = Path(self.package_path)
        if (package_path == root or root in package_path.parents or package_path in root.parents or
                private_tmp == root or root in private_tmp.parents):
            raise RouteError('GW_AGENT_CONFIG_INVALID')
        session_id = request.get('session_id')
        if session_id is not None:
            session_home = request.get('session_home')
            if not session_home:
                raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')
            try:
                home_path = Path(session_home)
                if not _is_private_dir(home_path):
                    raise ValueError('session home not private')
                home_resolved = home_path.resolve()
                if (home_resolved == root or
                        home_resolved in root.parents or
                        root in home_resolved.parents):
                    raise ValueError('session home inside or containing root')
                meta_bytes = private_read(home_path / 'meta.json', max_bytes=_SESSION_FILE_MAX_BYTES)
                history_bytes = private_read(home_path / 'history.json', max_bytes=_SESSION_FILE_MAX_BYTES)

                state_dir = Path(tmpdir) / 'state'
                sessions_dir = state_dir / 'sessions'
                dest_session_dir = sessions_dir / session_id
                for directory in (state_dir, sessions_dir, dest_session_dir):
                    try:
                        directory.mkdir(mode=0o700)
                    except FileExistsError:
                        pass
                    os.chmod(directory, 0o700)

                for filename, payload in (('meta.json', meta_bytes), ('history.json', history_bytes)):
                    target = dest_session_dir / filename
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(payload)
                    os.chmod(target, 0o600)
            except RouteError:
                raise
            except Exception:
                raise RouteError('GW_AGENT_SESSION_UNAVAILABLE') from None
        task = dict(request['task'])
        gateway_data_class = self.data_class_map.get(
            task.get('data_class'), task.get('data_class'))
        if gateway_data_class not in self.allowed_data_classes:
            raise RouteError('GW_AGENT_TASK_DATA_CLASS_INVALID')
        task['data_class'] = gateway_data_class
        credential = self._load_credential()
        profile = dict(self.profile)
        profile['allowed_data_classes'] = [
            self.data_class_map.get(item, item) for item in profile['allowed_data_classes']]
        profile['auth_env'] = 'GW_AGENT_API_KEY' if credential is not None else None
        try:
            serialized_task = _canonical(task)
            serialized_profile = _canonical({'schema_version': 1,
                                              'profiles': {self.profile_name: profile}})
        except (TypeError, ValueError):
            raise RouteError('GW_AGENT_REQUEST_INVALID') from None
        auth = {
            'schema_version': 1,
            'authorization_ref': self.authorization_ref,
            'checked_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'scope_digest': _scope_digest(task),
            'task_id': task['task_id'],
            'egress_basis': task['egress_basis'],
            'data_class': gateway_data_class,
            'profile': self.profile_name,
            'session_id': request.get('session_id'),
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
        session_id = request.get('session_id')
        cmd = ['resume', session_id] if session_id is not None else ['run']
        common_args = [
            '--authorization-file', str(Path(tmp) / 'authorization.json'),
            '--workdir', request['root'], '--profile', self.profile_name,
            '--task-file', str(Path(tmp) / 'task.json'),
            '--result-file', str(Path(tmp) / 'result.json'),
            '--audit-file', str(Path(tmp) / 'audit.jsonl'),
            '--profiles-file', str(Path(tmp) / 'profiles.json'),
            '--state-dir', str(Path(tmp) / 'state'),
        ]
        if _HAS_SAFE_PATH_FLAG:
            return [sys.executable, '-B', '-P', '-m', 'api_agent_gateway'] + cmd + common_args
        return [sys.executable, '-B', '-c', _SAFE_PATH_LAUNCHER] + cmd + common_args

    def _load_credential(self):
        if self._credential_resolved:
            return self._credential
        try:
            config_value = llm_config.resolve_profile_config('company-gw')
            try:
                credential = llm_config.api_key(config_value)
            except Exception as exc:
                diagnostic = getattr(exc, 'diagnostic', None)
                diagnostic_code = getattr(diagnostic, 'code', None)
                diagnostic_code = getattr(diagnostic_code, 'value', diagnostic_code)
                if diagnostic_code != 'AUTH_CREDENTIAL_MISSING':
                    raise
                credential = None
        except Exception:
            raise RouteError('GW_AGENT_CREDENTIAL_MISSING') from None
        if (credential is not None and
                (type(credential) is not str or not credential or '\x00' in credential or
                 len(credential) > 8192)):
            raise RouteError('GW_AGENT_CREDENTIAL_MISSING')
        self._credential = credential
        self._credential_resolved = True
        return credential

    def build_env(self, tmpdir):
        credential = self._load_credential()
        env = {key: os.environ[key] for key in
               ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE')
               if key in os.environ}
        env['TMPDIR'] = tmpdir
        env['PYTHONPATH'] = self.package_path
        if credential is not None:
            env['GW_AGENT_API_KEY'] = credential
        self._credential = None
        self._credential_resolved = False
        home = env.get('HOME') or str(Path.home())
        return github_identity.ai_github_env(env, home)

    def parse_output(self, stdout, stderr, log_text):
        result = self._result
        self._gateway_code = (result.get('code') if type(result) is dict and
                              type(result.get('code')) is str and
                              re.fullmatch(r'[A-Z0-9_]{1,64}', result['code']) else None)
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
                _MODEL.fullmatch(result['served_model']) is None or
                type(result.get('task_id')) is not str or
                type(result.get('run_id')) is not str or
                re.fullmatch(r'[0-9a-f]{24}', result['run_id']) is None or
                (result.get('session_id') is not None and type(result['session_id']) is not str) or
                type(result.get('usage')) is not dict or type(result.get('turns')) is not int or
                (result.get('budget_used') is not None and type(result['budget_used']) is not dict) or
                type(result.get('audit_sha256')) is not str or
                re.fullmatch(r'[0-9a-f]{64}', result['audit_sha256']) is None):
            raise RouteError('GW_AGENT_OUTPUT_INVALID')
        if (result['status'] == 'DONE' and
                (result['served_model'] == 'UNKNOWN' or
                 result['served_model'] in _ROUTING_ALIASES)):
            raise RouteError('GW_AGENT_MODEL_UNVERIFIED', facts={
                'gateway_code': self._gateway_code, 'gateway_status': result['status']})
        if ((result['status'] == 'DONE' and result['code'] is not None) or
                (result['status'] != 'DONE' and
                 (type(result['code']) is not str or not result['code']))):
            raise RouteError('GW_AGENT_OUTPUT_INVALID')
        self._status = result['status']
        self._gateway_code = result['code']
        if ((self.expected_served_model is None or
                self.expected_served_model in _ROUTING_ALIASES) and
                self._validated_request is not None):
            self._validated_request['model'] = result['served_model']
        return {'status': result['status'], 'summary': result['summary'][:4000],
                'session_id': result.get('session_id'),
                'usage': _normalize_gw_agent_usage(result.get('usage')),
                'changed_paths': result['changed_paths']}

    def validate_exit(self, exit_code, parsed):
        if type(exit_code) is not int or exit_code != _STATUS_EXIT.get(self._status):
            raise RouteError('GW_AGENT_OUTPUT_INVALID',
                             facts={'gateway_code': self._gateway_code})

    def validate_status(self, parsed):
        if self._status != 'DONE':
            code = self._gateway_code
            if code == 'MODEL_REPORTED_BLOCKED' and self._tool_failure is not None:
                code = self._tool_failure
            raise RouteError('GW_AGENT_' + self._status, facts={'gateway_code': code})

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
        if (type(model) is not str or _MODEL.fullmatch(model) is None or
                model == 'UNKNOWN' or model in _ROUTING_ALIASES):
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
            expected_task_id = (request.get('task', {}).get('task_id')
                                if type(request.get('task')) is dict else None)
            if (type(expected_task_id) is not str or
                    _TASK_ID.fullmatch(expected_task_id) is None or
                    type(self._result) is not dict or
                    self._result.get('task_id') != expected_task_id):
                code = (self._result.get('code') if type(self._result) is dict else None)
                status = (self._result.get('status') if type(self._result) is dict else None)
                suffix = (code if type(code) is str and
                          re.fullmatch(r'[A-Z0-9_]{1,48}', code) else None)
                diagnostic = 'GW_AGENT_TASK_ID_MISMATCH'
                if suffix is not None:
                    diagnostic += '_' + suffix
                raise RouteError(diagnostic[:64], facts={
                    'gateway_code': suffix,
                    'gateway_status': status if type(status) is str and
                    re.fullmatch(r'[A-Z_]{1,32}', status) else None})
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
            self._tool_failure = _last_tool_failure(lines)
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
            session_home = request.get('session_home')
            if session_home is not None:
                result_session_id = (self._result.get('session_id')
                                     if isinstance(self._result, dict) else None)
                requested_session_id = request.get('session_id')
                if (isinstance(result_session_id, str) and
                        _BINDING_SESSION_ID_READ.fullmatch(result_session_id) is not None and
                        (requested_session_id is None or result_session_id == requested_session_id)):
                    source_session_dir = Path(tmpdir) / 'state' / 'sessions' / result_session_id
                    meta_src = source_session_dir / 'meta.json'
                    history_src = source_session_dir / 'history.json'
                    if os.path.lexists(meta_src) and os.path.lexists(history_src):
                        try:
                            home_path = Path(session_home)
                            if not _is_private_dir(home_path):
                                raise ValueError('session home not private dir')
                            home_resolved = home_path.resolve()
                            if (home_resolved == root or
                                    home_resolved in root.parents or
                                    root in home_resolved.parents):
                                raise ValueError('session home inside or containing root')
                            meta_bytes = private_read(meta_src, max_bytes=_SESSION_FILE_MAX_BYTES)
                            history_bytes = private_read(history_src, max_bytes=_SESSION_FILE_MAX_BYTES)
                            private_write(home_path / 'meta.json', meta_bytes)
                            private_write(home_path / 'history.json', history_bytes)
                        except RouteError:
                            raise
                        except Exception:
                            raise RouteError('GW_AGENT_SESSION_PERSIST_FAILED') from None
        except RouteError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
            raise RouteError('GW_AGENT_OUTPUT_INVALID') from None


_BINDING_SESSION_ID_WRITE = re.compile(r'[0-9a-fA-F-]{16,64}\Z')
_BINDING_SESSION_ID_READ = re.compile(r'[0-9a-f]{32}\Z')
_BINDING_SCOPE_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_BINDING_FIELDS = frozenset({
    'data_class', 'executor_task_id', 'root_task_id',
    'schema_version', 'scope_digest', 'session_id'
})
_SESSION_HOME_PARENT_DIR = 'gw-agent-sessions'
_SESSION_BINDING_FILE = 'binding.json'
_SESSION_LOCK_FILE = 'lock'
_SESSION_BINDING_MAX_BYTES = 64 * 1024


def _is_private_dir(path: Path) -> bool:
    """Return True if path is a directory owned by user with mode 0700 (no group/world bits)."""
    try:
        info = Path(path).lstat()
        return (not stat.S_ISLNK(info.st_mode) and
                stat.S_ISDIR(info.st_mode) and
                info.st_uid == os.getuid() and
                (stat.S_IMODE(info.st_mode) & 0o077) == 0)
    except OSError:
        return False


def _is_inside_git(path: Path) -> bool:
    """Return True if path or any of its ancestors contains a .git entry."""
    try:
        cur = Path(path).absolute()
        for candidate in (cur, *cur.parents):
            if os.path.lexists(candidate / '.git'):
                return True
    except OSError:
        return True
    return False


def _ensure_private_dir(path: Path) -> None:
    """Create directory 0700 if missing; check ownership and mode if existing."""
    try:
        created = False
        try:
            path.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            pass
        if created:
            os.chmod(path, 0o700)
        if not _is_private_dir(path):
            raise RouteError('GW_AGENT_SESSION_HOME_UNSAFE')
    except RouteError:
        raise
    except (OSError, ValueError):
        raise RouteError('GW_AGENT_SESSION_HOME_UNSAFE') from None


def open_session_home(store_root, executor_task_id):
    """Open or create a private session home directory for an executor task."""
    if type(executor_task_id) is not str or _TASK_ID.fullmatch(executor_task_id) is None:
        raise RouteError('GW_AGENT_SESSION_HOME_UNSAFE')
    try:
        store_path = Path(store_root).absolute()
    except (TypeError, ValueError, OSError):
        raise RouteError('GW_AGENT_SESSION_HOME_UNSAFE') from None

    sessions_parent = store_path.parent / _SESSION_HOME_PARENT_DIR
    home = sessions_parent / executor_task_id

    _ensure_private_dir(sessions_parent)
    _ensure_private_dir(home)

    if _is_inside_git(home):
        raise RouteError('GW_AGENT_SESSION_HOME_UNSAFE')

    return home


def private_read(path, max_bytes=_SESSION_BINDING_MAX_BYTES):
    """Read a private regular file with strict permissions and bounded size."""
    p = Path(path)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if hasattr(os, 'O_CLOEXEC'):
        flags |= os.O_CLOEXEC
    try:
        fd = os.open(p, flags)
    except OSError as exc:
        raise ValueError('unable to open file') from exc
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or
                info.st_uid != os.getuid() or
                info.st_nlink != 1 or
                (stat.S_IMODE(info.st_mode) & 0o077) != 0 or
                info.st_size > max_bytes):
            raise ValueError('file violates private read constraints')
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError('file exceeds byte limit')
            chunks.append(chunk)
        return b''.join(chunks)
    finally:
        os.close(fd)


_private_read = private_read


def private_write(target_path, data):
    """Write data to target_path atomically using a mode 0600 temp file and rename."""
    target = Path(target_path)
    folder = target.parent
    if isinstance(data, str):
        payload = data.encode('utf-8')
    elif isinstance(data, (bytes, bytearray)):
        payload = bytes(data)
    else:
        raise TypeError('data must be bytes or str')

    temp_name = f'.tmp-{target.name}-{os.urandom(8).hex()}'
    temp_path = folder / temp_name

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if hasattr(os, 'O_CLOEXEC'):
        flags |= os.O_CLOEXEC

    fd = os.open(temp_path, flags, 0o600)
    renamed = False
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.rename(temp_path, target)
        renamed = True
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if not renamed:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
    return target


_private_write = private_write


class _LockAcquireDescriptor:
    """Descriptor enabling GwAgentSessionLock.acquire to be used on class or instance."""

    def __init__(self, func):
        self.func = func

    def __get__(self, instance, owner):
        if instance is None:
            def class_acquire(home, *args, **kwargs):
                lock = owner(home)
                lock.acquire(home, *args, **kwargs)
                return lock
            return class_acquire
        return self.func.__get__(instance, owner)


class GwAgentSessionLock:
    """Advisory flock-based lock bound to a session home directory."""

    def __init__(self, home=None):
        self._home = Path(home) if home is not None else None
        self._fd = None
        self._acquired = False

    def _acquire_impl(self, home=None):
        if self._acquired or self._fd is not None:
            raise RouteError('GW_AGENT_SESSION_LOCKED')
        target_home = Path(home) if home is not None else self._home
        if target_home is None:
            raise RouteError('GW_AGENT_SESSION_HOME_UNSAFE')
        self._home = target_home

        try:
            if not _is_private_dir(target_home) or _is_inside_git(target_home):
                raise RouteError('GW_AGENT_SESSION_HOME_UNSAFE')

            lock_path = target_home / _SESSION_LOCK_FILE
            create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            if hasattr(os, 'O_CLOEXEC'):
                create_flags |= os.O_CLOEXEC
            try:
                cfd = os.open(lock_path, create_flags, 0o600)
                try:
                    os.fchmod(cfd, 0o600)
                finally:
                    os.close(cfd)
            except FileExistsError:
                pass

            open_flags = os.O_RDWR | os.O_NOFOLLOW
            if hasattr(os, 'O_CLOEXEC'):
                open_flags |= os.O_CLOEXEC
            ofd = os.open(lock_path, open_flags)
            try:
                info = os.fstat(ofd)
                if (not stat.S_ISREG(info.st_mode) or
                        info.st_uid != os.getuid() or
                        info.st_nlink != 1 or
                        (stat.S_IMODE(info.st_mode) & 0o077) != 0):
                    raise RouteError('GW_AGENT_SESSION_HOME_UNSAFE')

                try:
                    fcntl.flock(ofd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (BlockingIOError, OSError) as exc:
                    if (isinstance(exc, BlockingIOError) or
                            getattr(exc, 'errno', None) in (
                                errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK)):
                        raise RouteError('GW_AGENT_SESSION_LOCKED') from None
                    raise RouteError('GW_AGENT_SESSION_HOME_UNSAFE') from None

                self._fd = ofd
                self._acquired = True
            except BaseException:
                os.close(ofd)
                raise
        except RouteError:
            raise
        except (OSError, ValueError):
            raise RouteError('GW_AGENT_SESSION_HOME_UNSAFE') from None
        return self

    acquire = _LockAcquireDescriptor(_acquire_impl)

    def release(self):
        """Release the held lock; safe to call multiple times."""
        if self._fd is not None:
            fd = self._fd
            self._fd = None
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
            except OSError:
                pass

    def __enter__(self):
        if not self._acquired:
            self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def session_binding_exists(home):
    """Return True if binding.json exists (lexists) in the session home."""
    try:
        return os.path.lexists(Path(home) / _SESSION_BINDING_FILE)
    except (OSError, TypeError, ValueError):
        return False


def write_session_binding(home, binding_or_root=None, executor_task_id=None,
                          session_id=None, data_class=None, scope_digest=None,
                          *, root_task_id=None, **kwargs):
    """Write canonical binding.json to session home with private write."""
    try:
        home_path = Path(home)
        if not _is_private_dir(home_path) or _is_inside_git(home_path):
            raise RouteError('GW_AGENT_SESSION_BINDING_FAILED')

        if isinstance(binding_or_root, dict):
            allowed_keys = {'schema_version', 'root_task_id', 'executor_task_id',
                            'session_id', 'data_class', 'scope_digest'}
            if set(binding_or_root.keys()) - allowed_keys:
                raise RouteError('GW_AGENT_SESSION_BINDING_FAILED')
            if kwargs or executor_task_id is not None or session_id is not None:
                raise RouteError('GW_AGENT_SESSION_BINDING_FAILED')
            schema_ver = binding_or_root.get('schema_version', 1)
            if type(schema_ver) is not int or schema_ver != 1:
                raise RouteError('GW_AGENT_SESSION_BINDING_FAILED')
            root_task_id = binding_or_root.get('root_task_id')
            executor_task_id = binding_or_root.get('executor_task_id')
            session_id = binding_or_root.get('session_id')
            data_class = binding_or_root.get('data_class')
            scope_digest = binding_or_root.get('scope_digest')
        else:
            if root_task_id is None:
                root_task_id = binding_or_root
            elif binding_or_root is not None:
                raise RouteError('GW_AGENT_SESSION_BINDING_FAILED')
            if 'data_class' in kwargs:
                data_class = kwargs.pop('data_class')
            if 'scope_digest' in kwargs:
                scope_digest = kwargs.pop('scope_digest')
            if 'session_id' in kwargs:
                session_id = kwargs.pop('session_id')
            if 'executor_task_id' in kwargs:
                executor_task_id = kwargs.pop('executor_task_id')
            if 'schema_version' in kwargs:
                s_ver = kwargs.pop('schema_version')
                if type(s_ver) is not int or s_ver != 1:
                    raise RouteError('GW_AGENT_SESSION_BINDING_FAILED')
            if kwargs:
                raise RouteError('GW_AGENT_SESSION_BINDING_FAILED')

        if (type(root_task_id) is not str or _TASK_ID.fullmatch(root_task_id) is None or
                type(executor_task_id) is not str or _TASK_ID.fullmatch(executor_task_id) is None or
                type(session_id) is not str or _BINDING_SESSION_ID_WRITE.fullmatch(session_id) is None or
                type(data_class) is not str or not 1 <= len(data_class) <= 64 or
                type(scope_digest) is not str or _BINDING_SCOPE_DIGEST.fullmatch(scope_digest) is None):
            raise RouteError('GW_AGENT_SESSION_BINDING_FAILED')

        payload_dict = {
            'data_class': data_class,
            'executor_task_id': executor_task_id,
            'root_task_id': root_task_id,
            'schema_version': 1,
            'scope_digest': scope_digest,
            'session_id': session_id,
        }
        encoded = _canonical(payload_dict) + b'\n'
        private_write(home_path / _SESSION_BINDING_FILE, encoded)
    except RouteError:
        raise
    except Exception:
        raise RouteError('GW_AGENT_SESSION_BINDING_FAILED') from None


def read_session_binding(home, *args, executor_task_id=None):
    """Read and validate binding.json from home for the given executor task ID."""
    if args:
        if executor_task_id is None and len(args) == 1:
            executor_task_id = args[0]
        else:
            raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')
    if type(executor_task_id) is not str or _TASK_ID.fullmatch(executor_task_id) is None:
        raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')

    try:
        home_path = Path(home)
        if not _is_private_dir(home_path) or _is_inside_git(home_path):
            raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')

        raw = private_read(home_path / _SESSION_BINDING_FILE, max_bytes=_SESSION_BINDING_MAX_BYTES)
        parsed = json.loads(
            raw.decode('utf-8'),
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
        )
        if type(parsed) is not dict or set(parsed.keys()) != _BINDING_FIELDS:
            raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')

        if type(parsed['schema_version']) is not int or parsed['schema_version'] != 1:
            raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')

        if parsed['executor_task_id'] != executor_task_id:
            raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')

        if (type(parsed['session_id']) is not str or
                _BINDING_SESSION_ID_READ.fullmatch(parsed['session_id']) is None):
            raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')

        if (type(parsed['root_task_id']) is not str or
                _TASK_ID.fullmatch(parsed['root_task_id']) is None):
            raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')

        if (type(parsed['data_class']) is not str or
                not 1 <= len(parsed['data_class']) <= 64):
            raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')

        if (type(parsed['scope_digest']) is not str or
                _BINDING_SCOPE_DIGEST.fullmatch(parsed['scope_digest']) is None):
            raise RouteError('GW_AGENT_SESSION_UNAVAILABLE')

        return parsed
    except RouteError:
        raise
    except Exception:
        raise RouteError('GW_AGENT_SESSION_UNAVAILABLE') from None


